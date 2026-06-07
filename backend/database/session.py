"""Async SQLAlchemy engine factory and URL transformation for NeonDB/asyncpg."""

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def _to_asyncpg_url(url: str) -> tuple[str, dict]:
    """Convert a postgresql:// URL to asyncpg format, stripping unsupported params.

    NeonDB URLs include sslmode=require and channel_binding=require; asyncpg handles
    SSL via connect_args and doesn't accept these as query parameters.
    """
    if not url:
        return url, {}

    url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    needs_ssl = "sslmode" in params
    for p in ("sslmode", "channel_binding"):
        params.pop(p, None)

    new_query = urlencode({k: v[0] for k, v in params.items()})
    clean_url = urlunparse(parsed._replace(query=new_query))
    connect_args = {"ssl": True} if needs_ssl else {}
    return clean_url, connect_args


def build_engine(settings) -> AsyncEngine:
    url, connect_args = _to_asyncpg_url(settings.DATABASE_URL)
    return create_async_engine(url, connect_args=connect_args, pool_size=5, max_overflow=10)


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
