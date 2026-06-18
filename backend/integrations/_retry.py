"""Shared tenacity retry config + connect/read timeouts for the integration clients.

All three external clients (S3, HuggingFace, DuckDuckGo) wrap their blocking
calls with the same retry policy: up to 3 attempts, exponential backoff
(0.5s multiplier, capped at 8s), re-raising the final error. Defining it once
keeps the magic numbers in a single place.

Spread it into the decorator: ``@retry(**RETRY_KW)``.

The same module also holds the connect/read timeouts each client passes to its
underlying transport (boto3 ``Config``, ``InferenceClient``, ``DDGS``). Without
them a hung upstream pins the request — and the worker thread it runs on — until
the process is killed, defeating the retry policy (a never-returning call never
raises, so tenacity never sees a transient error to retry). Seconds.

NOTE (overseer): these are module-level constants, not Settings fields, because
this work item is scoped to ``integrations/**`` and must not touch ``config.py``.
If they should become tunable, lift them into ``Settings`` and pass them through
the ``from_settings`` constructors.
"""

from tenacity import stop_after_attempt, wait_exponential

RETRY_KW = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=8),
    reraise=True,
)

# Connect/read timeouts (seconds) for the external clients' underlying transports.
# Connect is kept short (a dead host should fail fast and let @retry fire); read is
# more generous to tolerate a slow-but-alive upstream (e.g. HF model cold-start).
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 30.0
