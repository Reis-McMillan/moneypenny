import json
import uuid

from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Router, Route
from sse_starlette.sse import EventSourceResponse

from modules.agent import Agent
from middleware.authenticated import User


class ChatBody(BaseModel):
    message: str


async def create_chat(request: Request):
    body = ChatBody(**await request.json())
    user: User = request.user

    if not user.mcp_token:
        return JSONResponse(
            status_code=403,
            content={
                "setup_required": True,
                "redirect_url": request.app.state.verys_client.mcp_auth_url
            }
        )

    thread_id = str(uuid.uuid4())

    email_db = request.app.state.db.email
    agent = await Agent.build(thread_id, email_db, user)

    async def event_stream():
        async for token in agent.chat(body.message):
            yield {"event": "token", "data": token}

        title = await agent.generate_title(body.message)
        yield {"event": "metadata", "data": json.dumps({"thread_id": thread_id, "title": title})}

    return EventSourceResponse(event_stream())


async def send_message(request: Request):
    body = ChatBody(**await request.json())
    thread_id = request.path_params['thread_id']
    user: User = request.user

    if not user.mcp_token:
        return JSONResponse(
            status_code=403,
            content={
                "setup_required": True,
                "redirect_url": request.app.state.verys_client.mcp_auth_url
            }
        )

    email_db = request.app.state.db.email
    agent = await Agent.build(thread_id, email_db, user)

    async def event_stream():
        async for token in agent.chat(body.message):
            yield {"event": "token", "data": token}

    return EventSourceResponse(event_stream())


async def get_chat(request: Request):
    thread_id = request.path_params['thread_id']
    user = request.user

    email_db = request.app.state.db.email
    agent = await Agent.build(thread_id, email_db, user)
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