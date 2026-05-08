import json
import logging
import uuid
from datetime import datetime, timezone

import openai
from pydantic import BaseModel
from starlette.exceptions import HTTPException
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


async def _ensure_mcp_token(request: Request) -> JSONResponse | None:
    user: User = request.user
    verys_client = request.app.state.verys_client
    user.auth = await verys_client.check_mcp_token(user.auth)
    user.mcp_token = user.auth.get('mcp_token')
    if not user.mcp_token:
        return _setup_required_response(verys_client.mcp_auth_url)
    return None


async def _require_owned_chat(request: Request, thread_id: str) -> dict:
    chat = await request.app.state.db.chat.get(thread_id)
    if not chat or chat['owner'] != request.user.user_id:
        raise HTTPException(status_code=404, detail='Chat not found.')
    return chat


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

    if (resp := await _ensure_mcp_token(request)) is not None:
        return resp

    thread_id = str(uuid.uuid4())

    email_db = request.app.state.db.email
    chat_db = request.app.state.db.chat
    try:
        agent = await Agent.build(thread_id, email_db, user, body.token_id)
    except MCPConsentRequired as e:
        return _setup_required_response(e.redirect_url)

    async def event_stream():
        async for evt in _safe_stream(agent.chat(body.message)):
            yield evt

        try:
            title = await agent.generate_title(body.message)
            now = datetime.now(timezone.utc)
            await chat_db.upsert({
                'thread_id': thread_id,
                'owner': user.user_id,
                'title': title,
                'created_at': now,
                'updated_at': now,
            })
            yield {"event": "metadata", "data": json.dumps({"thread_id": thread_id, "title": title})}
        except Exception:
            logger.exception("Title generation failed")

    return EventSourceResponse(event_stream())


async def send_message(request: Request):
    body = ChatBody(**await request.json())
    thread_id = request.path_params['thread_id']
    user: User = request.user

    if (resp := await _ensure_mcp_token(request)) is not None:
        return resp

    await _require_owned_chat(request, thread_id)

    email_db = request.app.state.db.email
    chat_db = request.app.state.db.chat
    try:
        agent = await Agent.build(thread_id, email_db, user, body.token_id)
    except MCPConsentRequired as e:
        return _setup_required_response(e.redirect_url)

    async def event_stream():
        try:
            async for evt in _safe_stream(agent.chat(body.message)):
                yield evt
        finally:
            await chat_db.touch(thread_id)

    return EventSourceResponse(event_stream())


async def get_chat(request: Request):
    thread_id = request.path_params['thread_id']
    user: User = request.user

    if (resp := await _ensure_mcp_token(request)) is not None:
        return resp

    chat = await _require_owned_chat(request, thread_id)

    email_db = request.app.state.db.email
    try:
        # -1 provided as token_id as place holder... not used in route
        agent = await Agent.build(thread_id, email_db, user, -1)
    except MCPConsentRequired as e:
        return _setup_required_response(e.redirect_url)
    messages = agent.get_history()

    return JSONResponse({
        "thread_id": thread_id,
        "title": chat['title'],
        "messages": [
            {"role": msg.type, "content": msg.content}
            for msg in messages
            if msg.type in ("human", "ai")
        ]
    })


async def list_chats(request: Request):
    user: User = request.user

    chats = await request.app.state.db.chat.list_for_owner(user.user_id)
    for c in chats:
        c['created_at'] = c['created_at'].isoformat()
        c['updated_at'] = c['updated_at'].isoformat()
    return JSONResponse({'chats': chats})


async def resume_chat(request: Request):
    body = ResumeBody(**await request.json())
    thread_id = request.path_params['thread_id']
    user: User = request.user

    if (resp := await _ensure_mcp_token(request)) is not None:
        return resp

    await _require_owned_chat(request, thread_id)

    email_db = request.app.state.db.email
    chat_db = request.app.state.db.chat
    try:
        agent = await Agent.build(thread_id, email_db, user, body.token_id)
    except MCPConsentRequired as e:
        return _setup_required_response(e.redirect_url)

    async def event_stream():
        try:
            async for evt in _safe_stream(agent.resume(body.decisions)):
                yield evt
        finally:
            await chat_db.touch(thread_id)

    return EventSourceResponse(event_stream())


async def draft_email(request: Request):
    body = DraftBody(**await request.json())
    user: User = request.user

    if (resp := await _ensure_mcp_token(request)) is not None:
        return resp

    incoming_thread_id = request.path_params.get('thread_id')
    is_new_chat = incoming_thread_id is None
    thread_id = incoming_thread_id or str(uuid.uuid4())

    existing_chat = None
    if not is_new_chat:
        existing_chat = await _require_owned_chat(request, thread_id)

    email_db = request.app.state.db.email
    chat_db = request.app.state.db.chat
    try:
        agent = await Agent.build(thread_id, email_db, user, body.token_id)
    except MCPConsentRequired as e:
        return _setup_required_response(e.redirect_url)

    if existing_chat:
        title = existing_chat['title']
    else:
        title = await agent.generate_title(
            f"Draft email to {body.recipient} about {body.content}"
        )

    async def event_stream():
        try:
            async for evt in _safe_stream(agent.draft_email(
                content=body.content,
                recipient=body.recipient,
                recipient_email=body.recipient_email,
            )):
                yield evt

            now = datetime.now(timezone.utc)
            if is_new_chat:
                await chat_db.upsert({
                    'thread_id': thread_id,
                    'owner': user.user_id,
                    'title': title,
                    'created_at': now,
                    'updated_at': now,
                })
            else:
                await chat_db.touch(thread_id)

            yield {"event": "metadata", "data": json.dumps({"thread_id": thread_id, "title": title})}
        except Exception:
            logger.exception("draft_email stream failed")

    return EventSourceResponse(event_stream())