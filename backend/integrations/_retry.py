"""Shared tenacity retry config for the integration clients.

All three external clients (S3, HuggingFace, DuckDuckGo) wrap their blocking
calls with the same retry policy: up to 3 attempts, exponential backoff
(0.5s multiplier, capped at 8s), re-raising the final error. Defining it once
keeps the magic numbers in a single place.

Spread it into the decorator: ``@retry(**RETRY_KW)``.
"""

from tenacity import stop_after_attempt, wait_exponential

RETRY_KW = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=8),
    reraise=True,
)
