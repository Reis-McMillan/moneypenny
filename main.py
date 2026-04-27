import asyncio
from contextlib import asynccontextmanager
import logging
from starlette.applications import Starlette
from starlette.datastructures import State
from starlette.routing import Route
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware

from config import config
from db.base import Base
from db.email import Email
from db.auth_cache import AuthCache
from db.authorization import Authorization
from middleware.authenticated import BearerToken, on_authenticated_error
from routes.auth import initialize, callback
from routes.accounts import get_linked_accounts, refresh_linked_accounts, add_linked_account
from routes.chat import create_chat, send_message, get_chat
from modules.ingest.embed import Embedder
from modules.ingest.service import Service
from modules.ingest.gmail_service import GmailService  # noqa: F401 — registers provider
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

    await Base.ensure_collections([
        app.state.db.email,
        app.state.db.auth_cache,
        app.state.db.authorization
    ])

    await app.state.db.authorization.ensure_indexes()
    await app.state.db.auth_cache.ensure_indexes()
    await app.state.db.email.ensure_search_index()

    app.state.verys_client = VerysClient(app.state.db.auth_cache)
    app.state.queue = asyncio.Queue()
    app.state.embedder = Embedder(
        app.state.queue,
        app.state.db.email
    )
    app.state.embedder.start()
    app.state.services = {}

    Service.set_auth_cache(app.state.db.auth_cache)
    Service.set_email_db(app.state.db.email)
    Service.set_queue(app.state.queue)
    Service.set_services(app.state.services)
    Service.set_verys_client(app.state.verys_client)

    yield

routes = [
    Route('/auth/initialize', initialize),
    Route('/auth/callback', callback),
    Route('/accounts', get_linked_accounts, methods=['GET']),
    Route('/accounts/refresh', refresh_linked_accounts, methods=['POST']),
    Route('/accounts/link', add_linked_account, methods=['GET']),
    Route('/chat', create_chat, methods=['POST']),
    Route('/chat/{thread_id}', send_message, methods=['POST']),
    Route('/chat/{thread_id}', get_chat, methods=['GET']),
]

app = Starlette(
    lifespan=lifespan,
    routes=routes,
    middleware=[
        Middleware(
            AuthenticationMiddleware,
            backend=BearerToken(),
            on_error=on_authenticated_error
        )
    ]
)
