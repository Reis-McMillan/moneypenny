import json
import logging
import uuid

import openai
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Router, Route
from sse_starlette.sse import EventSourceResponse

from modules.agent import Agent, MCPConsentRequired
from middleware.authenticated import User

logger = logging.getLogger(__name__)


def _setup_required_response(redirect_url: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "setup_required": True,
            "redirect_url": redirect_url,
        }
    )


def _error_event(code: str, message: str) -> dict:
    return {"event": "error", "data": json.dumps({"code": code, "message": message})}


async def _safe_stream(gen):
    try:
        async for kind, text in gen:
            yield {"event": kind, "data": text}
    except openai.BadRequestError as e:
        body = getattr(e, "body", None)
        detail = (body or {}).get("error", {}) if isinstance(body, dict) else {}
        message = detail.get("message") if isinstance(detail, dict) else None
        logger.warning("Model bad request: %s", message or str(e))
        yield _error_event("model_bad_request", message or str(e))
    except Exception as e:
        logger.exception("Unhandled error in chat stream")
        yield _error_event("internal_error", str(e))


class ChatBody(BaseModel):
    message: str
    token_id: int


class DraftBody(BaseModel):
    token_id: int
    content: str
    recipient: str
    recipient_email: str


class ResumeBody(BaseModel):
    token_id: int
    decisions: list[dict]


async def create_chat(request: Request):
    body = ChatBody(**await request.json())
    user: User = request.user

    if not user.mcp_token:
        return _setup_required_response(request.app.state.verys_client.mcp_auth_url)

    thread_id = str(uuid.uuid4())

    email_db = request.app.state.db.email
    try:
        agent = await Agent.build(thread_id, email_db, user, body.token_id)
    except MCPConsentRequired as e:
        return _setup_required_response(e.redirect_url)

    async def event_stream():
        async for evt in _safe_stream(agent.chat(body.message)):
            yield evt

        try:
            title = await agent.generate_title(body.message)
            yield {"event": "metadata", "data": json.dumps({"thread_id": thread_id, "title": title})}
        except Exception:
            logger.exception("Title generation failed")

    return EventSourceResponse(event_stream())


async def send_message(request: Request):
    body = ChatBody(**await request.json())
    thread_id = request.path_params['thread_id']
    user: User = request.user

    if not user.mcp_token:
        return _setup_required_response(request.app.state.verys_client.mcp_auth_url)

    email_db = request.app.state.db.email
    try:
        agent = await Agent.build(thread_id, email_db, user, body.token_id)
    except MCPConsentRequired as e:
        return _setup_required_response(e.redirect_url)

    async def event_stream():
        async for evt in _safe_stream(agent.chat(body.message)):
            yield evt

    return EventSourceResponse(event_stream())


async def get_chat(request: Request):
    thread_id = request.path_params['thread_id']
    user: User = request.user

    if not user.mcp_token:
        return _setup_required_response(request.app.state.verys_client.mcp_auth_url)

    email_db = request.app.state.db.email
    try:
        # -1 provided as token_id as place holder... not used in route
        agent = await Agent.build(thread_id, email_db, user, -1)
    except MCPConsentRequired as e:
        return _setup_required_response(e.redirect_url)
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


async def list_chats(request: Request):
    pass


async def resume_chat(request: Request):
    body = ResumeBody(**await request.json())
    thread_id = request.path_params['thread_id']
    user: User = request.user

    if not user.mcp_token:
        return _setup_required_response(request.app.state.verys_client.mcp_auth_url)

    email_db = request.app.state.db.email
    try:
        agent = await Agent.build(thread_id, email_db, user, body.token_id)
    except MCPConsentRequired as e:
        return _setup_required_response(e.redirect_url)

    async def event_stream():
        async for evt in _safe_stream(agent.resume(body.decisions)):
            yield evt

    return EventSourceResponse(event_stream())


async def draft_email(request: Request):
    body = DraftBody(**await request.json())
    user: User = request.user

    if not user.mcp_token:
        return _setup_required_response(request.app.state.verys_client.mcp_auth_url)

    thread_id = request.path_params.get('thread_id') or str(uuid.uuid4())

    email_db = request.app.state.db.email
    try:
        agent = await Agent.build(thread_id, email_db, user, body.token_id)
    except MCPConsentRequired as e:
        return _setup_required_response(e.redirect_url)
    
    title = agent.get_title()
    if not title:
        title = await agent.generate_title(
            f"Draft email to {body.recipient} about {body.content}"
        )

    async def event_stream():
        async for evt in _safe_stream(agent.draft_email(
            content=body.content,
            recipient=body.recipient,
            recipient_email=body.recipient_email,
        )):
            yield evt

        yield {"event": "metadata", "data": json.dumps({"thread_id": thread_id, "title": title})}

    return EventSourceResponse(event_stream())