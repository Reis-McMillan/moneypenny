import logging
from datetime import datetime
import httpx
import jwt
from urllib.parse import urlencode

from config import config
from db.auth_cache import AuthCache, SyncAuthCache
from db.action import Action, SyncAction


logger = logging.getLogger(__name__)


def _parse_expires_at(token: dict) -> dict:
    raw = token.get('expires_at')
    if isinstance(raw, str):
        token['expires_at'] = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    return token


class BaseVerysClient:
    def __init__(self):
        self.token_url = f"{config.AUTH_URL}/token"
        self.federation_url = f"{config.AUTH_URL}/federation/"
        self.mcp_auth_url = f"{config.MCP_URL}/auth/initialize"

    def token_expired(self, token: str | bytes) -> bool:
        try:
            jwt.decode(
                token,
                options={"verify_signature": False, "verify_exp": True}
            )
            return False
        except jwt.ExpiredSignatureError:
            return True
        
    @staticmethod
    def _insert_external_tokens(
        auth: dict,
        ext_token: dict | list[dict]
    ):
        # if ext_tokens is list, overwrite
        if isinstance(ext_token, list):
            auth['external_tokens'] = ext_token
            return auth
        
        elif isinstance(ext_token, dict):
            if auth['external_tokens'] is None:
                auth['external_tokens'] = [ext_token]
                return auth
            token_ids: list = list(map(lambda t: t['token_id'], auth['external_tokens']))
            try:
                idx = token_ids.index(ext_token['token_id'])
                auth['external_tokens'][idx] = ext_token
            except ValueError:
                auth['external_tokens'].append(ext_token)
            
            return auth
        
    @staticmethod
    def find_token(tokens: list[dict], token_id: int) -> dict | None:
        for t in tokens:
            if t['token_id'] == token_id:
                return t
        return None

class VerysClient(BaseVerysClient):
    
    def __init__(self, auth_cache: AuthCache, action: Action):
        super().__init__()
        self.auth_cache: AuthCache = auth_cache
        self.action: Action = action
    
    async def refresh_access_token(
        self,
        auth: dict
    ) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.token_url,
                data={
                    "grant_type": "refresh_token",
                    "client_id": config.CLIENT_ID,
                    "client_secret": config.CLIENT_SECRET,
                    "refresh_token": auth['refresh_token']
                }
            )

        if not response.is_success:
            raise RuntimeError(f"Token refresh failed: {response.status_code} {response.text}")

        data = response.json()
        auth['access_token'] = data['access_token']
        auth['refresh_token'] = data['refresh_token']

        await self.auth_cache.upsert(auth)

        return auth
    
    async def check_token(
        self,
        auth: dict,
    ) -> dict:
        if self.token_expired(auth['access_token']):
            logger.info("Access token expired for %s, refreshing", auth['email'])
            auth = await self.refresh_access_token(auth)

        return auth

    async def mcp_token_exchange(
        self,
        auth: dict
    ) -> dict:
        auth = await self.check_token(auth)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.token_url,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "client_id": config.CLIENT_ID,
                    "client_secret": config.CLIENT_SECRET,
                    "subject_token": auth['access_token'],
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "audience": config.MCP_CLIENT_ID,
                    "scope": "mcp"
                }
            )

        if not response.is_success:
            logger.warning(
                f"MCP token exchange failed: {response.status_code} {response.text}"
            )
            return auth

        data = response.json()
        auth['mcp_token'] = data['access_token']
        
        await self.auth_cache.upsert(auth)

        return auth

    async def get_external_tokens(
        self,
        auth: dict,
        token_id: int | None = None
    ) -> dict:
        auth = await self.check_token(auth)

        async with httpx.AsyncClient() as client:
            if token_id:
                federation_url = f"{self.federation_url}{token_id}"
            else:
                federation_url = f"{self.federation_url}tokens"
            response = await client.get(
                federation_url,
                headers={
                    "Authorization": f"Bearer {auth['access_token']}"
                },
            )

        if not response.is_success:
            if (token_id and response.status_code == 401 and 
                response.json().get('error') == 'reauthorization_required'):
                token = self.find_token(auth['external_tokens'], token_id)
                reauth_url = (self.federation_url 
                              + f'initiate?{urlencode({'provider_id': token['provider_id']})}')
                await self.action.upsert({
                    'user_id': auth['user_id'],
                    'token_id': token_id,
                    'token_email': token['email'],
                    'reauth_url': reauth_url
                })
            raise RuntimeError(f"External token fetch failed: {response.status_code} {response.text}")

        data: list[dict] | dict = response.json()
        if isinstance(data, list):
            data = [_parse_expires_at(t) for t in data]
        elif isinstance(data, dict):
            data = _parse_expires_at(data)
        auth = self._insert_external_tokens(auth, data)
        await self.auth_cache.upsert(auth)

        return auth


class SyncVerysClient(BaseVerysClient):
    def __init__(self, auth_cache: SyncAuthCache, action: SyncAction):
        super().__init__()
        self.auth_cache = auth_cache
        self.action = action

    def refresh_access_token(
        self,
        auth: dict
    ) -> dict:
        with httpx.Client() as client:
            response = client.post(
                self.token_url,
                data={
                    "grant_type": "refresh_token",
                    "client_id": config.CLIENT_ID,
                    "client_secret": config.CLIENT_SECRET,
                    "refresh_token": auth['refresh_token']
                }
            )

        if not response.is_success:
            raise RuntimeError(f"Token refresh failed: {response.status_code} {response.text}")

        data = response.json()
        auth['access_token'] = data['access_token']
        auth['refresh_token'] = data['refresh_token']

        self.auth_cache.upsert(auth)

        return auth
    
    def check_token(
        self,
        auth: dict,
    ) -> dict:
        if self.token_expired(auth['access_token']):
            logger.info("Access token expired for %s, refreshing", auth['email'])
            auth = self.refresh_access_token(auth)

        return auth
    
    def mcp_token_exchange(
        self,
        auth: dict
    ) -> dict:
        auth = self.check_token(auth)
        with httpx.Client() as client:
            response = client.post(
                self.token_url,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "client_id": config.CLIENT_ID,
                    "client_secret": config.CLIENT_SECRET,
                    "subject_token": auth['access_token'],
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "audience": config.MCP_CLIENT_ID,
                    "scope": "mcp"
                }
            )

        if not response.is_success:
            logger.warning(
                f"MCP token exchange failed: {response.status_code} {response.text}"
            )
            return auth

        data = response.json()
        auth['mcp_token'] = data['access_token']
        
        self.auth_cache.upsert(auth)

        return auth

    def get_external_tokens(
        self,
        auth: dict,
        token_id: int | None = None
    ) -> dict:
        auth = self.check_token(auth)

        with httpx.Client() as client:
            if token_id:
                federation_url = f"{self.federation_url}{token_id}"
            else:
                federation_url = f"{self.federation_url}tokens"
            response = client.get(
                federation_url,
                headers={
                    "Authorization": f"Bearer {auth['access_token']}"
                },
            )

        if not response.is_success:
            if (token_id and response.status_code == 401 and 
                response.json().get('error') == 'reauthorization_required'):
                token = self.find_token(auth['external_tokens'], token_id)
                reauth_url = (self.federation_url 
                              + f'initiate?{urlencode({'provider_id': token['provider_id']})}')
                self.action.upsert({
                    'user_id': auth['user_id'],
                    'token_id': token_id,
                    'token_email': token['email'],
                    'reauth_url': reauth_url
                })
            raise RuntimeError(f"External token fetch failed: {response.status_code} {response.text}")

        data: list[dict] | dict = response.json()
        if isinstance(data, list):
            data = [_parse_expires_at(t) for t in data]
        elif isinstance(data, dict):
            data = _parse_expires_at(data)
        auth = self._insert_external_tokens(auth, data)
        self.auth_cache.upsert(auth)

        return auth