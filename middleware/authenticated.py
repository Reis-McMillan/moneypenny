import jwt
from starlette.authentication import (
    AuthenticationBackend, AuthenticationError, SimpleUser, AuthCredentials
)

import config
from db.auth_cache import AuthCache
from utils.jwks import get_public_key


class User(SimpleUser):
    def __init__(self, username, auth_cache: AuthCache):
        super().__init__(username)
        auth = auth_cache.get(username)
        self.access_token = auth['access_token']
        self.refresh_token = auth['refresh_token']
        self.external_tokens = auth['external_tokens']


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

        username = decoded['sub']
        auth_cache = conn.request.app.state.db.auth_cache
        return AuthCredentials(["authenticated"]), User(username, auth_cache)
