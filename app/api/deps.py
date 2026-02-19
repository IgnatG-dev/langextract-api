"""
FastAPI dependency-injection helpers.

Provides ``Depends()``-compatible generators for shared resources
such as Redis connections.
"""

from __future__ import annotations

from collections.abc import Generator

import redis

from app.core.config import get_redis_pool


def get_redis() -> Generator[redis.Redis, None, None]:
    """Yield a Redis client backed by the shared connection pool.

    Usage as a FastAPI dependency::

        @router.get("/ping")
        def ping(r: redis.Redis = Depends(get_redis)):
            return r.ping()
    """
    client = redis.Redis(connection_pool=get_redis_pool())
    try:
        yield client
    finally:
        client.close()
