import os
import asyncpg
from fastapi import Depends
from auth import verify_token

APP_DB_URL = os.environ.get(
    "APP_DB_URL",
    "postgresql://appuser:apppassword@app-db:5432/appdb",
)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=10)
    return _pool


async def get_user_db_conn(claims: dict = Depends(verify_token)):
    """
    Yields a connection with `app.user_id` set for this request's authenticated
    user (from the verified JWT's `sub` claim) -- every RLS policy in
    init-schema.sql checks against this session variable. Because it's a
    FastAPI dependency, every endpoint that uses it gets the isolation
    automatically; there's no code path where a query can run without it set.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # set_config's third arg (is_local=true) scopes this to the current
        # transaction/connection only -- it never leaks across pooled connections
        await conn.execute(
            "SELECT set_config('app.user_id', $1, true)", claims["sub"]
        )
        yield conn