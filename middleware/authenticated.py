import jwt
from starlette.authentication import (
    AuthenticationBackend, AuthenticationError, SimpleUser, AuthCredentials
)
from starlette.responses import JSONResponse, PlainTextResponse

from config import config
from db.auth_cache import AuthCache
from utils.jwks import get_public_key
from modules.tokens import VerysClient


class User(SimpleUser):
    def __init__(self, auth: dict):
        super().__init__(auth['email'])
        self.user_id = auth['user_id']
        self.access_token = auth['access_token']
        self.refresh_token = auth['refresh_token']
        self.external_tokens = auth.get('external_tokens')
        self.mcp_token = auth['mcp_token']
        self.roles = auth.get('roles', [])
        self.auth = auth

    @property
    def is_admin(self) -> bool:
        return 'admin' in self.roles


class AuthCacheMissing(AuthenticationError):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


PUBLIC_PATHS = {
    ('GET', '/auth/initialize'),
    ('GET', '/auth/callback'),
    ('POST', '/test-users'),
}


class BearerToken(AuthenticationBackend):
    async def authenticate(self, conn):
        auth = conn.headers.get("Authorization")
        if not auth:
            if (conn.scope['method'], conn.url.path) in PUBLIC_PATHS:
                return
            raise AuthenticationError('Missing auth token.')

        try:
            scheme, credentials = auth.split()
            if scheme.lower() != 'bearer':
                return
            decoded = jwt.decode(
                credentials,
                await get_public_key(),
                algorithms=['EdDSA'],
                audience=config.CLIENT_ID
            )
        except jwt.InvalidTokenError:
            raise AuthenticationError('Invalid auth token.')

        user_id = int(decoded['sub'])
        auth_cache: AuthCache = conn.app.state.db.auth_cache
        auth = await auth_cache.get(user_id)        
        if not auth:
            raise AuthCacheMissing('User session not found.')
        
        verys_client: VerysClient = conn.app.state.verys_client
        auth = await verys_client.check_mcp_token(auth)

        return AuthCredentials(["authenticated"]), User(auth)
    

def on_authenticated_error(request, exc):
    if isinstance(exc, AuthCacheMissing):
        return JSONResponse(
            status_code=403,
            content={
                "setup_required": True,
                "redirect_url": config.INIT_URI
            }
        )
    return PlainTextResponse(str(exc), status_code=400)