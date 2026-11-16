"""
handlers/ask_errors.py
──────────────────────
Error taxonomy for /ask: turns any exception into ONE accurate, user-friendly
message instead of a guess based on a substring.

WHY THIS EXISTS (the bug being fixed)
────────────────────────────────────
The old handler did ``if "model" in str(e)`` before checking for rate limits.
A Gemini 429 error literally says "...The model gemini-3.5-flash-lite is
overloaded", and a 503 says "The model is overloaded" — so users were told the
MODEL NAME WAS WRONG whenever the API was merely busy. Guessing from text is
fragile; here we read the HTTP status first and use narrow, unambiguous phrases
only as a fallback.

STRUCTURAL CHANGE: each failure class has its own small handler function in
``_HANDLERS``. They run in priority order, so a transient 429/503 can never be
swallowed by a later auth/model handler. The module imports no Telegram, Gemini
or Redis code, so the whole table is unit-testable in isolation.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

# Only these 3-digit numbers are treated as HTTP codes when scanning raw error
# text. Scanning for any number would misread token counts, ports, timestamps...
_HTTP_CODE_RE = re.compile(r"(?<!\d)(400|401|403|404|408|429|500|502|503|504)(?!\d)")


class AskSafetyBlocked(Exception):
    """Gemini refused to answer because of a safety / content filter."""


class AskEmptyAnswer(Exception):
    """Gemini returned a response with no text at all."""


EMPTY_PROMPT_MESSAGE = (
    "🤖 Please add a question after /ask — e.g. /ask why is the sky blue?"
)


@dataclass(frozen=True)
class AskFailure:
    category: str        # short tag for logs
    message: str         # what the user actually sees
    retryable: bool = False   # transient: release the per-user cooldown
    silent: bool = False      # nothing worth showing (e.g. "not modified")


@dataclass(frozen=True)
class _Context:
    exc: Exception
    status: int | None
    status_name: str
    text: str
    type_names: frozenset[str]

    def mentions(self, *markers: str) -> bool:
        return any(m in self.text or m in self.status_name for m in markers)

    def has_type(self, *markers: str) -> bool:
        return any(m in name for name in self.type_names for m in markers)


def _chain(exc: Exception):
    """Walk the exception and its __cause__/__context__ chain without looping."""
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)


def _http_status(exc: Exception) -> int | None:
    """
    Pull an HTTP code from google-genai APIError (.code), requests/httpx
    (.response.status_code), or google-api-core exceptions (.code as IntEnum).
    Falls back to a narrow regex over the message.
    """
    for node in _chain(exc):
        for attr in ("code", "status_code", "http_status"):
            value = getattr(node, attr, None)
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and 100 <= value <= 599:
                return value
            if isinstance(value, str) and value.isdigit() and 100 <= int(value) <= 599:
                return int(value)
        response = getattr(node, "response", None)
        value = getattr(response, "status_code", None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    match = _HTTP_CODE_RE.search(str(exc))
    return int(match.group(1)) if match else None


def _build_context(exc: Exception) -> _Context:
    status_name = ""
    for node in _chain(exc):
        value = getattr(node, "status", None)
        if isinstance(value, str) and value:
            status_name = value.casefold()
            break
    type_names = frozenset(
        cls.__name__.casefold()
        for node in _chain(exc)
        for cls in type(node).__mro__
    )
    return _Context(
        exc=exc,
        status=_http_status(exc),
        status_name=status_name,
        text=str(exc).casefold(),
        type_names=type_names,
    )


# ── Distinct handlers, in priority order ─────────────────────────────────────
# IMPORTANT: busy/overload MUST come before model/auth. Their error strings
# routinely contain the model name, which is what caused the original bug.

def _handle_benign(ctx: _Context):
    # Telegram rejects an edit that produces identical text. Not a real failure.
    if ctx.status is None and "message is not modified" in ctx.text:
        return AskFailure("not_modified", "", silent=True)
    return None


def _handle_busy(ctx: _Context):
    """429 / 503 / 500 / 502 — high demand, quota or temporary overload."""
    if ctx.status in (429, 503, 500, 502):
        return AskFailure(
            "busy",
            "⚠️ The AI is under high demand right now (rate-limited or overloaded). "
            "Nothing is misconfigured — please try again in a minute.",
            retryable=True,
        )
    # Text markers are only trusted when there is no contradictory HTTP status.
    # A 404 saying "model unavailable in your region" is a MODEL problem, not
    # high demand — this guard prevents that second kind of misreporting.
    if ctx.status is not None and ctx.status not in (429, 503, 500, 502):
        return None
    if ctx.has_type("resourceexhausted", "serviceunavailable") or ctx.mentions(
        "resource_exhausted", "resource has been exhausted", "overloaded",
        "high demand", "rate limit", "ratelimit", "too many requests", "quota",
        "unavailable", "service unavailable", "try again later",
    ):
        return AskFailure(
            "busy",
            "⚠️ The AI is under high demand right now (rate-limited or overloaded). "
            "Nothing is misconfigured — please try again in a minute.",
            retryable=True,
        )
    return None


def _handle_timeout(ctx: _Context):
    if isinstance(ctx.exc, (asyncio.TimeoutError, TimeoutError)):
        return AskFailure(
            "timeout",
            "⏱ The AI took too long to respond and the request timed out. "
            "Please try again in a moment.",
            retryable=True,
        )
    if (
        ctx.status in (408, 504)
        or ctx.mentions("deadline_exceeded", "deadline exceeded", "timed out")
        or ctx.has_type("timeout", "timedout", "deadlineexceeded")
    ):
        return AskFailure(
            "timeout",
            "⏱ The AI took too long to respond and the request timed out. "
            "Please try again in a moment.",
            retryable=True,
        )
    return None


def _handle_network(ctx: _Context):
    if ctx.has_type(
        "connectionerror", "connecterror", "networkerror", "sslerror",
        "gaierror", "socketerror", "proxyerror", "remotedisconnected",
    ) or ctx.mentions(
        "connection refused", "name or service not known", "failed to establish",
        "network is unreachable", "dns", "ssl:",
    ):
        return AskFailure(
            "network",
            "📡 I couldn't reach the AI service (network problem). "
            "Please try again shortly.",
            retryable=True,
        )
    return None


def _handle_safety(ctx: _Context):
    if isinstance(ctx.exc, AskSafetyBlocked) or ctx.mentions(
        "block_reason", "prohibited_content", "finish_reason: safety",
        "safety filter", "content filter", "blocked by safety",
    ):
        return AskFailure(
            "safety",
            "🛡 That question was blocked by the AI's safety filter. "
            "Try rephrasing it.",
        )
    return None


def _handle_auth(ctx: _Context):
    # 403 = key is valid but lacks permission / API not enabled.
    if (
        ctx.status == 403
        or ctx.mentions("permission_denied", "permission denied", "unauthorized", "forbidden")
        or ctx.has_type("permissiondenied")
    ):
        return AskFailure(
            "auth_permission",
            "🔒 The AI key doesn't have permission to use this model. "
            "Please ask the bot admin to check the key and enable the Gemini API.",
        )
    # 401 = key missing / invalid / expired.
    if (
        ctx.status == 401
        or ctx.mentions("unauthenticated", "api key", "api_key", "invalid credential", "authentication")
        or ctx.has_type("unauthenticated")
    ):
        return AskFailure(
            "auth_key",
            "🔑 The AI API key is invalid or missing. "
            "Please ask the bot admin to update GEMINI_API_KEY.",
        )
    return None


def _handle_model(ctx: _Context):
    # Narrow phrases only — NEVER the bare word "model".
    if (
        ctx.status == 404
        or ctx.mentions(
            "not_found", "model not found", "not found for api version",
            "unknown model", "invalid model", "unsupported model",
            "model is not supported", "publisher model",
        )
        or ctx.has_type("notfound")
    ):
        return AskFailure(
            "model",
            "🧩 The configured AI model was not found. "
            "Please ask the bot admin to fix GEMINI_MODEL.",
        )
    return None


def _handle_bad_request(ctx: _Context):
    if ctx.status == 400 or ctx.mentions("invalid_argument", "bad request"):
        return AskFailure(
            "bad_request",
            "⚠️ The AI rejected this request — usually an invalid model setting "
            "or an unsupported question format. Ask the bot admin to check "
            "GEMINI_MODEL, or try rephrasing.",
        )
    return None


def _handle_empty_answer(ctx: _Context):
    if isinstance(ctx.exc, AskEmptyAnswer) or ctx.mentions("empty response", "no text in the response"):
        return AskFailure(
            "empty_answer",
            "🤖 The AI returned no answer (often a safety filter). "
            "Try rephrasing your question.",
            retryable=True,
        )
    return None


def _handle_telegram_limit(ctx: _Context):
    # Telegram caps a message at 4096 characters. Our chunking should prevent
    # this, but if it ever happens the user deserves a specific explanation.
    if ctx.has_type("badrequest") and ctx.mentions("too long", "message_too_long"):
        return AskFailure(
            "telegram_too_long",
            "📏 The answer exceeded Telegram's 4096-character limit and was cut short. "
            "Try asking for a shorter answer.",
        )
    return None


_HANDLERS = (
    _handle_benign,
    _handle_telegram_limit,   # very specific (PTB BadRequest + "too long")
    _handle_busy,
    _handle_timeout,
    _handle_network,
    _handle_safety,
    _handle_auth,
    _handle_model,
    _handle_bad_request,
    _handle_empty_answer,
)

_UNKNOWN = AskFailure(
    "unknown",
    "❌ The AI request failed unexpectedly. The error has been logged — "
    "please try again in a moment.",
)


def classify_ask_error(exc: Exception) -> AskFailure:
    """Return the single most accurate failure description for an exception."""
    ctx = _build_context(exc)
    for handler in _HANDLERS:
        failure = handler(ctx)
        if failure is not None:
            return failure
    return _UNKNOWN
