import jwt
from starlette.authentication import (
    AuthenticationBackend, AuthenticationError, SimpleUser, AuthCredentials
)

from config import config
from utils.jwks import get_public_key


class User(SimpleUser):
    def __init__(self, auth: dict):
        super().__init__(auth['email'])
        self.user_id = auth['user_id']
        self.access_token = auth['access_token']
        self.refresh_token = auth['refresh_token']
        self.external_tokens = auth.get('external_tokens')


class BearerToken(AuthenticationBackend):
    async def authenticate(self, conn):
        if "Authorization" not in conn.headers:
            return

        auth = conn.headers.get("Authorization")
        if not auth:
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
        auth_cache = conn.app.state.db.auth_cache
        cached_auth = await auth_cache.get(user_id)
        if not cached_auth:
            raise AuthenticationError('User session not found.')
        return AuthCredentials(["authenticated"]), User(cached_auth)
