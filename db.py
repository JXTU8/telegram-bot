# db.py
import os
import sys
from upstash_redis import Redis

try:
    redis = Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )
except KeyError as missing:
    sys.exit(f"❌ Redis env var {missing} is not set. Exiting.")
except Exception as e:
    sys.exit(f"❌ Failed to initialise Redis client: {e}")