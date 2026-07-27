import os
from mem0 import Memory

LITELLM_URL = os.environ.get(
    "LITELLM_URL",
    "http://litellm:4000"
)

LITELLM_KEY = os.environ.get(
    "LITELLM_MASTER_KEY",
    ""
)

_mem0_config = {

    "vector_store": {
        "provider": "pgvector",
        "config": {
            "host": os.environ.get("APP_DB_HOST", "app-db"),
            "port": 5432,
            "dbname": "appdb",
            "user": "appuser",
            "password": "apppassword",
            "collection_name": "mem0_memories",
        },
    },

    "llm": {
        "provider": "openai",
        "config": {
            "openai_base_url": LITELLM_URL + "/v1",
            "api_key": LITELLM_KEY,
            "model": "claude-primary",
        },
    },

    "embedder": {
        "provider": "openai",
        "config": {
            "openai_base_url": LITELLM_URL + "/v1",
            "api_key": LITELLM_KEY,
            "model": "text-embedding-3-small",
        },
    },
    "embedder": {
        "provider": "openai",
        "config": {
            "api_key": os.environ.get("LITELLM_MASTER_KEY"),
            "openai_base_url": os.environ.get(
                "LITELLM_URL",
                "http://litellm:4000"
            ) + "/v1",
            "model": "text-embedding-primary",
        },
    },
}


_memory: Memory | None = None


def get_memory() -> Memory:
    global _memory

    if _memory is None:
        _memory = Memory.from_config(_mem0_config)

    return _memory


def add_turn_memory(user_id: str, messages: list[dict]) -> None:
    get_memory().add(
        messages,
        user_id=user_id
    )


def search_relevant_memory(
    user_id: str,
    query: str,
    limit: int = 5
) -> list[dict]:

    result = get_memory().search(
        query=query,
        user_id=user_id,
        limit=limit
    )

    return (
        result.get("results", result)
        if isinstance(result, dict)
        else result
    )