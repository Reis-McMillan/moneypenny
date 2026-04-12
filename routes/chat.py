import json
import uuid

from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Router, Route
from sse_starlette.sse import EventSourceResponse

from modules.agent import Agent


class ChatBody(BaseModel):
    message: str


async def create_chat(request: Request):
    body = ChatBody(**await request.json())
    username = request.user.username
    auth_cache = request.app.state.db.auth_cache
    thread_id = str(uuid.uuid4())

    agent = await Agent.build(thread_id, username, auth_cache)

    async def event_stream():
        async for token in agent.chat(body.message):
            yield {"event": "token", "data": token}

        title = await agent.generate_title(body.message)
        yield {"event": "metadata", "data": json.dumps({"thread_id": thread_id, "title": title})}

    return EventSourceResponse(event_stream())


async def send_message(request: Request):
    body = ChatBody(**await request.json())
    thread_id = request.path_params['thread_id']
    username = request.user.username
    auth_cache = request.app.state.db.auth_cache

    agent = await Agent.build(thread_id, username, auth_cache)

    async def event_stream():
        async for token in agent.chat(body.message):
            yield {"event": "token", "data": token}

    return EventSourceResponse(event_stream())


async def get_chat(request: Request):
    thread_id = request.path_params['thread_id']
    username = request.user.username
    auth_cache = request.app.state.db.auth_cache

    agent = await Agent.build(thread_id, username, auth_cache)
    messages = agent.get_history()

    return JSONResponse({
        "thread_id": thread_id,
        "title": agent.get_title(),
        "messages": [
            {"role": msg.type, "content": msg.content}
            for msg in messages
            if msg.type in ("human", "ai")
        ]
    })

chat_router = Router([
    Route("/", endpoint=create_chat, methods=["POST"]),
    Route("/{thread_id}", endpoint=send_message, methods=["POST"]),
    Route("/{thread_id}", endpoint=get_chat, methods=["GET"])
])