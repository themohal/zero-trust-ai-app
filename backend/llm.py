import os
import httpx

LITELLM_URL = os.environ.get("LITELLM_URL", "http://litellm:4000")
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")


async def stream_chat_completion(message: str, model: str = "claude-primary"):
    """
    Streams tokens from LiteLLM (which routes to Claude, falling back to
    OpenAI automatically on repeated failure) as Server-Sent Events.
    The backend never talks to Anthropic/OpenAI directly -- LiteLLM is the
    only thing holding provider API keys.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            f"{LITELLM_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {LITELLM_MASTER_KEY}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": message}],
                "stream": True,
            },
        ) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    yield f"{line}\n\n"
    yield "event: done\ndata: [DONE]\n\n"
