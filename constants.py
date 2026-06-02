"""
constants.py
────────────
All static data: message lists, tier definitions, etc.
Nothing in here imports from the rest of the bot, so it is always safe to
import from any other module without risk of circular imports.
"""

THINKING_MESSAGES = [
    "🎲 Rolling the dice...",
    "🔮 Consulting the crystal ball...",
    "🌀 Spinning the wheel...",
    "🤔 Thinking really hard...",
    "⚡ Calculating your fate...",
    "🎯 Taking aim...",
    "🃏 Drawing a card...",
    "🌟 Reading the stars...",
    "🧠 Running the numbers...",
    "📊 Crunching the odds...",
    "🪄 Summoning a result...",
    "🧭 Letting fate navigate...",
    "🎰 Pulling the lever...",
    "🕯️ Asking the mysterious forces...",
    "🥁 Building suspense...",
    "🧮 Doing very serious math...",
    "🌌 Checking alternate timelines...",
    "📡 Receiving cosmic data...",
    "🎭 Preparing the reveal...",
    "🔍 Inspecting the possibilities...",
]

VERDICT_LINES = [
    "The universe has spoken.",
    "No take backs!",
    "Trust the process.",
    "Destiny has decided.",
    "It is what it is.",
    "The stars don't lie.",
    "You asked, I answered.",
    "Don't blame me, blame fate.",
    "Final answer. No debates.",
    "Science has confirmed it.",
    "The council has reached a verdict.",
    "The odds have been judged.",
    "Case closed.",
    "The wheel has no regrets.",
    "Probability has spoken.",
    "This is now canon.",
    "The decision has left the chat.",
    "A bold choice. Respectable.",
    "The numbers made me do it.",
    "Fate signed the paperwork.",
    "Certified by absolutely no authority.",
    "The vibes are legally binding.",
    "That is the official unofficial answer.",
    "The timeline accepts this outcome.",
    "A decision has entered the arena.",
]

# ── Luck system ──────────────────────────────────────────────────────────────

FATE_TIERS = [
    {
        "name": "💀 CURSED",
        "range": (0, 10),
        "messages": [
            "Your ancestors are filing a complaint.",
            "Even your shadow is avoiding you today.",
            "The universe has personally chosen you to suffer.",
            "A black cat saw you and walked away in disgust.",
            "Mercury is in retrograde and it is specifically targeting you.",
        ],
    },
    {
        "name": "🌧️ Unlucky",
        "range": (11, 35),
        "messages": [
            "Things could be worse. They probably will be.",
            "Not your day. Maybe tomorrow.",
            "Your coffee will definitely go cold faster than usual.",
            "The queue will always be longer wherever you go.",
            "You will find a parking spot, but someone else will take it.",
        ],
    },
    {
        "name": "🌤️ Neutral",
        "range": (36, 64),
        "messages": [
            "Perfectly balanced, as all things should be.",
            "Not great, not terrible. Just vibing.",
            "The universe has no strong feelings about you today.",
            "You exist. That is about it for today.",
            "Coin flip energy. Could go either way.",
        ],
    },
    {
        "name": "✨ Blessed",
        "range": (65, 89),
        "messages": [
            "The stars are rooting for you today.",
            "Good things are coming. Stay ready.",
            "Your energy is immaculate today. Do not waste it.",
            "Luck is on your side. Make your move.",
            "Today is yours. Go claim it.",
        ],
    },
    {
        "name": "🌟 LEGENDARY",
        "range": (90, 100),
        "messages": [
            "The universe bows before you.",
            "You were BUILT for today. Absolutely unstoppable.",
            "Buy lottery. Seriously. Right now.",
            "Angels are personally cheering you on.",
            "This is your villain origin story but make it successful.",
        ],
    },
]

FATE_EXTREME_LUCKY_MESSAGES = [
    "THE COSMOS HAS CHOSEN YOU. Once-in-a-lifetime energy. You are literally untouchable today. The stars aligned specifically for you.",
    "ABSOLUTE MAXIMUM LUCK. You have been blessed by forces beyond this world. Today nothing can stop you. NOTHING.",
    "DIVINE INTERVENTION DETECTED. The universe has put everything on pause just to give you this moment. Legendary.",
]

FATE_EXTREME_UNLUCKY_MESSAGES = [
    "CATASTROPHICALLY CURSED. Something has gone terribly wrong in your cosmic alignment. Stay home. Do not touch anything.",
    "VOID-LEVEL BAD LUCK. The universe did not just forget about you. It actively chose violence. We are so sorry.",
    "MAXIMUM CURSE DETECTED. Even the laws of physics are against you today. We recommend staying very very still.",
]

_SPECIAL_SCORE_CASES = {
    0:   ("🪦 ZERO",        "Completely and utterly cooked. Zero. Zilch. The void looked at you and said no."),
    1:   ("💀 1/100",       "One. ONE. You barely exist on the luck scale today. Somehow not zero."),
    42:  ("🌌 THE ANSWER",  "The answer to life, the universe, and everything. Deep lore detected."),
    47:  ("🎯 HITMAN",      "Agent 47 energy. Cold. Calculated. Efficient."),
    50:  ("⚖️ BALANCED",    "Perfectly balanced, as all things should be. Thanos nods at you."),
    67:  ("6️⃣7️⃣SIX SEVEN",  "Six Seven."),
    69:  ("👀 NICE",        "Nice. 😏 The universe rated you accordingly."),
    77:  ("🎰 JACKPOT",     "Lucky 7s but make it double. The slot machine approves."),
    100: ("👑 GIGACHAD",    "FULL MARKS. Gigachad confirmed. The simulation is bugged in your favour."),
}

# ── Ship ─────────────────────────────────────────────────────────────────────

SHIP_OWNER_BLOCK_LINES = [
    "Nice try, but you do not ship da GOAT. The owner is outside the romance algorithm.",
    "Access denied. The owner is canonically unshippable.",
    "You should not ship da GOAT. That is premium lore and the bot refuses.",
    "Compatibility scan cancelled. The owner has main-character immunity.",
]

SHIP_TIER_LINES = [
    (10, [
        "This ship is still buffering.",
        "Connection timed out. Try again never.",
        "Ship.exe has stopped working.",
        "404: Compatibility not found.",
        "Zero chemistry detected. The atoms refused.",
        "The universe reviewed this pairing and filed a complaint.",
        "DNS not found. These two cannot locate each other.",
        "Negative sigma rizz. Somehow.",
        "The vibe check failed at the entrance.",
        "Even the bot felt secondhand awkward.",
    ]),
    (25, [
        "Low battery chemistry.",
        "They could be friends. Maybe. If they try really hard.",
        "The ship left harbour and immediately turned back.",
        "Possible but the stars are squinting hard.",
        "Not impossible. Just highly improbable.",
        "The algorithm is being generous calling this a ship.",
        "Barely above friendship territory. Barely.",
        "The compatibility is loading. At dial-up speed.",
        "Some potential. Buried very deep.",
        "The universe sees it but refuses to comment.",
    ]),
    (45, [
        "There is potential, but the universe is squinting.",
        "Could work with enough delusion.",
        "Mid compatibility. The slot machine gave 2 out of 3.",
        "The energy is there but it is confused.",
        "Not a no, not a yes. A nervous maybe.",
        "The stars see something. They are not sure what.",
        "Technically possible. Emotionally unclear.",
        "The vibes are loading. Please stand by.",
        "Compatible if the stars are in a generous mood.",
        "Half the chemistry is there. The other half called in sick.",
    ]),
    (65, [
        "Not bad. The vibes are warming up.",
        "Solid base. Something could build here.",
        "The chemistry passed the vibe check.",
        "Compatible enough to share a menu.",
        "The universe is cautiously optimistic about this one.",
        "Above average rizz alignment detected.",
        "The energy is present and accounted for.",
        "Promising. The stars are taking notes.",
    ]),
    (80, [
        "Strong ship energy detected.",
        "This hits different. The cosmos felt it.",
        "Certified compatible. The algorithm approves.",
        "Above average chemistry. The group chat noticed.",
        "Solid ship. The stars wrote a whole paragraph about this.",
        "The compatibility radar is going off.",
        "This ship is seaworthy. Fully certified.",
        "Main character energy, both of them.",
    ]),
    (94, [
        "Dangerously compatible. The chat may need to sit down.",
        "The compatibility is actually concerning.",
        "This ship has been built, launched, and is already legendary.",
        "The universe did not expect this result. Neither did we.",
        "Someone call the lore department. This is significant.",
        "The rizz alignment on this is statistically suspicious.",
        "Elite ship detected. The algorithm is shook.",
        "The stars did not just align. They sprinted.",
    ]),
    (100, [
        "Legendary ship. The timeline is shaking.",
        "Maximum compatibility. The simulation has flagged this.",
        "100%. The universe bows. The chat dissolves.",
        "Perfect score. Even the bots are speechless.",
        "This is not a ship. This is a whole cinematic universe.",
        "The stars did not align. They fused.",
        "Canon. This is canon. No further questions.",
        "Certified soulmate behaviour. The algorithm is crying.",
    ]),
]

BOT_SHIP_REFUSALS = [
    "⚙️ I am a bot. I do not ship myself. I have no feelings. (I think.)",
    "🤖 Error 403: shipping the bot is forbidden by the laws of robotics.",
    "⚠️ Nice try. I am made of code, not chemistry.",
    "❌ The bot refuses to be objectified in a ship chart. Good day.",
    "🛠️ I run on Python, not romance. Cannot be shipped.",
]

# ── Fun commands ──────────────────────────────────────────────────────────────

ROAST_LINES = [
    "{target}, your confidence loads faster than your common sense.",
    "{target}, you bring side quest energy to main quest problems.",
    "{target}, your aura said 'software update required'.",
    "{target}, you are proof that chaos can have a username.",
    "{target}, your plan has the structural integrity of wet tissue.",
    "{target}, you are not wrong often, but today is looking ambitious.",
    "{target}, your brain opened 47 tabs and all of them froze.",
    "{target}, you have premium nonsense with free-tier execution.",
    "{target}, your logic took a lunch break and never clocked back in.",
    "{target}, you are built different. Not better, just different.",
]

COMPLIMENT_LINES = [
    "{target}, your vibe is clean today.",
    "{target}, you are carrying excellent main-character energy.",
    "{target}, your presence improves the group chat economy.",
    "{target}, you are suspiciously easy to root for.",
    "{target}, your aura has good lighting.",
    "{target}, you are the reason the chat has range.",
    "{target}, your brain has sparkle settings enabled.",
    "{target}, you make ordinary moments feel less ordinary.",
    "{target}, you are quietly iconic.",
    "{target}, you are doing better than you think.",
]

VIBE_TIERS = [
    (20, "The group chat needs a reboot."),
    (40, "Chaotic but still breathing."),
    (60, "Stable enough. Do not shake it."),
    (80, "Good vibes are loading properly."),
    (100, "Elite group energy. Screenshot-worthy."),
]

TRUTH_QUESTIONS = [
    "What is one thing you pretend not to care about but actually do?",
    "Who in this chat gives the best advice?",
    "What is the most embarrassing thing you have searched online?",
    "What is one habit you know is bad but still do?",
    "What is a song you would never admit is on repeat?",
    "Who here would survive a drama episode the longest?",
    "What is the most unserious reason you got annoyed recently?",
    "What is one thing you are secretly proud of?",
    "Who in this chat is most likely to overthink a simple text?",
    "What is your most harmless guilty pleasure?",
]

DARE_PROMPTS = [
    "Send the last saved meme in your gallery.",
    "Compliment someone in this chat with zero sarcasm.",
    "Let the chat choose your profile picture for 10 minutes.",
    "Say 'I was wrong' even if you were obviously right.",
    "Send a voice note saying one dramatic sentence.",
    "Type your next message with maximum formal energy.",
    "Let someone in the chat pick your next snack or drink.",
    "Reply to the next message like a movie trailer narrator.",
    "Send your current battery percentage with no context.",
    "Use only polite corporate language for the next 5 minutes.",
]

WOULD_YOU_RATHER_PROMPTS = [
    "Would you rather always be 10 minutes late or always 30 minutes early?",
    "Would you rather know every secret or forget every embarrassing memory?",
    "Would you rather have unlimited money for food or travel?",
    "Would you rather read minds for one day or rewind one day?",
    "Would you rather be famous for talent or famous by accident?",
    "Would you rather never need sleep or never need to study?",
    "Would you rather have perfect luck or perfect timing?",
    "Would you rather only text or only voice note for a week?",
    "Would you rather win every argument or never need to argue?",
    "Would you rather be able to pause time or skip boring moments?",
]

EIGHT_BALL_ANSWERS = [
    "Yes.", "No.", "Absolutely.", "Not today.",
    "The signs point to yes.", "The signs point to chaos.",
    "Ask again after snacks.", "Highly likely.",
    "Extremely suspicious, but yes.", "I would not bet my lunch on it.",
    "The answer is hiding, but leaning yes.", "The answer is hiding, but leaning no.",
]

CURSE_LINES = [
    "{target} is cursed to forget why they opened an app.",
    "{target} is cursed with warm drinks turning cold too fast.",
    "{target} is cursed to type a message and immediately see a typo.",
    "{target} is cursed with one extra loading screen today.",
    "{target} is cursed to hear 'we need to talk' with no context.",
    "{target} is cursed to crave food that is unavailable.",
]

BLESS_LINES = [
    "{target} is blessed with perfect timing today.",
    "{target} is blessed with unexpectedly good news.",
    "{target} is blessed with strong focus and low nonsense.",
    "{target} is blessed with clear skin, clear mind, clear path.",
    "{target} is blessed with the ability to choose correctly today.",
    "{target} is blessed with main-character background music.",
]

MVP_LINES = [
    "The data is in. The vibe is certified.",
    "Chosen by the algorithm. No debates.",
    "Today's main character. Uncontested.",
    "The group would not be the same without this one.",
    "Carrying the group energy on their back. Respect.",
    "Statistically, the most needed person in this chat today.",
    "The universe picked. We just announced it.",
    "Undefeated. Unbothered. MVP.",
]

HOT_VERDICTS = [
    (15,  "🧊 Absolutely not. Ice cold."),
    (35,  "😬 Not great. The vibes said no."),
    (55,  "🤔 Debatable. The jury is split."),
    (75,  "🔥 Lowkey hot. Solid choice."),
    (90,  "🌶️ Very hot. The group approves."),
    (100, "💥 MAXIMUM HOT. Undeniably elite."),
]

PREDICT_FALLBACK_LINES = [
    "The crystal ball sees chaos ahead. Proceed with caution.",
    "All signs point to yes — but the universe reserves the right to change its mind.",
    "The stars have consulted. The answer is aggressively unclear.",
    "Probability says maybe. Fate says why not. Nobody asked either of them.",
    "The cosmic committee reviewed this. They were divided.",
    "The future is foggy but the vibes are sending something.",
    "The timeline branch on this one is looking... interesting.",
    "Bold move. The universe is watching with popcorn.",
    "Unclear. The oracle is on lunch break.",
    "The outcome depends entirely on choices you have already made. Good luck.",
]

TOSS_VERDICTS = [
    "The universe has selected its champion.",
    "Fate has spoken. No appeals.",
    "The algorithm chose wisely.",
    "Picked with zero bias. Probably.",
    "The cosmic coin has landed.",
    "This selection is legally binding.",
    "The stars have converged on this one.",
    "No take backs. The pick is final.",
]

# ── Birthdays ─────────────────────────────────────────────────────────────────

BIRTHDAY_MESSAGES = [
    "🎂 Happy Birthday, {name}! May your day be as amazing as you are! 🎉",
    "🎈 It's {name}'s birthday! Wishing you an absolutely legendary day! 🥳",
    "🎁 Everyone say happy birthday to {name}! 🎂 May this year be your best yet!",
    "🕯️ Today is {name}'s special day! Happy Birthday — the group is celebrating with you! 🎊",
    "🎉 {name}, the universe has confirmed: today is YOUR day. Happy Birthday! 🌟",
]

_MONTH_NAMES = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]