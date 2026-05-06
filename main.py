from contextlib import asynccontextmanager
import logging
from starlette.applications import Starlette
from starlette.datastructures import State
from starlette.routing import Route
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.middleware.cors import CORSMiddleware

from config import config
from db.base import Base
from db.email import Email
from db.auth_cache import AuthCache
from db.authorization import Authorization
from db.action import Action
from db.chat import Chat
from middleware.authenticated import BearerToken, on_authenticated_error
from routes.auth import initialize, callback
from routes.accounts import get_linked_accounts, refresh_linked_accounts, add_linked_account
from routes.chat import create_chat, send_message, get_chat, list_chats, draft_email, resume_chat
from routes.ingest import get_counts, get_status, trigger_ingest
from modules.tokens import VerysClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    app.state.sessions = {}
    app.state.db = State()
    app.state.db.email = Email()
    app.state.db.auth_cache = AuthCache()
    app.state.db.authorization = Authorization()
    app.state.db.action = Action()
    app.state.db.chat = Chat()

    await Base.ensure_collections([
        app.state.db.email,
        app.state.db.auth_cache,
        app.state.db.authorization,
        app.state.db.action,
        app.state.db.chat,
    ])

    await app.state.db.authorization.ensure_indexes()
    await app.state.db.auth_cache.ensure_indexes()
    await app.state.db.action.ensure_indexes()
    await app.state.db.chat.ensure_indexes()
    await app.state.db.email.ensure_search_index()

    app.state.verys_client = VerysClient(
        app.state.db.auth_cache, app.state.db.action
    )

    yield

routes = [
    Route('/auth/initialize', initialize),
    Route('/auth/callback', callback),
    Route('/accounts', get_linked_accounts, methods=['GET']),
    Route('/accounts/refresh', refresh_linked_accounts, methods=['POST']),
    Route('/accounts/link', add_linked_account, methods=['GET']),
    Route('/chat', create_chat, methods=['POST']),
    Route('/chat', list_chats, methods=['GET']),
    Route('/chat/{thread_id}', send_message, methods=['POST']),
    Route('/chat/{thread_id}', get_chat, methods=['GET']),
    Route('/draft-email', draft_email, methods=['POST']),
    Route('/chat/{thread_id}/draft-email', draft_email, methods=['POST']),
    Route('/chat/{thread_id}/resume', resume_chat, methods=['POST']),
    Route('/ingest/counts', get_counts, methods=['GET']),
    Route('/ingest/status', get_status, methods=['GET']),
    Route('/ingest/trigger', trigger_ingest, methods=['POST']),
]

app = Starlette(
    lifespan=lifespan,
    routes=routes,
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=config.ALLOWED_ORIGINS,
            allow_credentials=True,
            allow_methods=['GET', 'PUT', 'POST', 'DELETE', 'OPTIONS'],
            allow_headers=['Authorization', 'Content-Type']
        ),
        Middleware(
            AuthenticationMiddleware,
            backend=BearerToken(),
            on_error=on_authenticated_error
        )
    ]
)
