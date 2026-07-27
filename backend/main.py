from fastapi import FastAPI, Depends, Request
from auth import verify_token, require_role
from token_exchange import exchange_for_scoped_token
from db import get_user_db_conn
from fastapi.responses import StreamingResponse
from llm import stream_chat_completion
from fastapi import BackgroundTasks
from memory import search_relevant_memory, add_turn_memory
 


app = FastAPI(title="zero-trust-ai-app backend")


# ---- ACT 1: authentication only ----
@app.get("/me")
async def me(claims: dict = Depends(verify_token)):
    return {
        "message": "Token verified successfully",
        "username": claims.get("preferred_username"),
        "subject": claims.get("sub"),
        "expires_at": claims.get("exp"),
    }


# ---- ACT 2: authentication + role-based authorization ----
@app.get("/conversations")
async def list_conversations(claims: dict = Depends(require_role("conversations:read"))):
    return {"conversations": ["chat-1", "chat-2"], "user": claims.get("preferred_username")}


@app.post("/conversations/{conv_id}/messages")
async def send_message(conv_id: str, claims: dict = Depends(require_role("conversations:write"))):
    return {"status": "sent", "conversation": conv_id, "user": claims.get("preferred_username")}


# @app.post("/conversations/{conv_id}/tools/invoke")
# async def invoke_tool(conv_id: str, claims: dict = Depends(require_role("tools:invoke"))):
#     return {"status": "tool invoked", "conversation": conv_id, "user": claims.get("preferred_username")}


@app.post("/conversations/{conv_id}/tools/invoke")
async def invoke_tool(
    conv_id: str,
    request: Request,
    claims: dict = Depends(require_role("tools:invoke"))
):

    body = await request.json()

    tool_name = body["tool_name"]
    arguments = body["arguments"]


    if tool_name == "calculator":

        expression = arguments["expression"]

        result = eval(expression)

        return {
            "status":"success",
            "tool":tool_name,
            "input":expression,
            "result":result,
            "user":claims.get("preferred_username")
        }


    return {
        "error":"unknown tool"
    }

# ---- ACT 3: agent identity + token exchange ----
@app.post("/agent/invoke-tool")
async def agent_invoke_tool(request: Request, claims: dict = Depends(verify_token)):
    """
    Takes Alice's own verified token, then -- using the backend's own SPIRE-issued
    workload identity, not a static secret -- exchanges it for a new token narrowly
    scoped to only 'tools:invoke'. That scoped token is what would actually be used
    to call a tool, never Alice's original, broader token.
    """
    auth_header = request.headers.get("Authorization")
    alice_token = auth_header.split(" ", 1)[1]

    exchange_result = await exchange_for_scoped_token(alice_token, requested_scope="tools-invoke")

    return {
        "message": "Exchanged Alice's token for a scoped agent token",
        "scoped_token": exchange_result["access_token"],
    }


# ---- Phase 4: RLS-backed data access, proves isolation end to end ----
from db import get_user_db_conn
import asyncpg

@app.post("/memories")
async def add_memory(request: Request, claims: dict = Depends(verify_token), conn=Depends(get_user_db_conn)):
    body = await request.json()
    await conn.execute(
        "INSERT INTO memories (owner_id, content) VALUES ($1, $2)",
        claims["sub"], body["content"],
    )
    return {"status": "stored"}

@app.get("/memories")
async def list_memories(claims: dict = Depends(verify_token), conn=Depends(get_user_db_conn)):
    # No WHERE owner_id = ... needed here -- RLS enforces it at the database
    # level regardless of what this query says. Try removing app.user_id
    # from the session (comment out db.py's set_config call) and this will
    # return zero rows even for a real, valid, authenticated user -- that's
    # the isolation working as designed, not a bug.
    rows = await conn.fetch("SELECT id, content, created_at FROM memories ORDER BY created_at DESC")
    return {"memories": [dict(r) for r in rows]}


@app.post("/memories")
async def add_memory(request: Request, claims: dict = Depends(verify_token),
                      conn=Depends(get_user_db_conn)):
    body = await request.json()
    await conn.execute(
        "INSERT INTO memories (owner_id, content) VALUES ($1, $2)",
        claims["sub"], body["content"],
    )
    return {"status": "stored"}


@app.get("/memories")
async def list_memories(
    claims: dict = Depends(verify_token),
    conn=Depends(get_user_db_conn),
):
    user_id = await conn.fetchval(
        "SELECT current_setting('app.user_id', true)"
    )
    return {"user_id": user_id}

#Phase 5

 
# @app.post("/chat/stream")
# async def chat_stream(request: Request,
#                        claims: dict = Depends(require_role("conversations:write"))):
#     """
#     Act 1 (verify_token, via require_role) + Act 2 (conversations:write) gate
#     this exactly like /conversations/{id}/messages -- the only difference is
#     the response streams back token-by-token instead of returning at once.
#     """
#     body = await request.json()
#     return StreamingResponse(
#         stream_chat_completion(body["message"]),
#         media_type="text/event-stream",
#     )
 
#Phase 6


@app.post("/chat/stream")
async def chat_stream(request: Request, background_tasks: BackgroundTasks,
                       claims: dict = Depends(require_role("conversations:write"))):
    body = await request.json()
    user_message = body["message"]
    user_id = claims["sub"]
 
    # Retrieve relevant long-term memory for THIS user only -- Mem0's
    # user_id filter is the isolation boundary here, same principle as the
    # RLS policies in Phase 4, different mechanism.
    relevant = search_relevant_memory(user_id, user_message, limit=5)
    memory_context = "\n".join(m.get("memory", "") for m in relevant) if relevant else ""
 
    prompt = (
        f"Relevant context about this user:\n{memory_context}\n\n"
        f"User message: {user_message}"
        if memory_context else user_message
    )
 
    background_tasks.add_task(
        add_turn_memory, user_id,
        [{"role": "user", "content": user_message}],
    )
 
    return StreamingResponse(
        stream_chat_completion(prompt),
        media_type="text/event-stream",
    )