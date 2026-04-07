import logging
from datetime import datetime, timezone

import httpx
import jwt

import config
from db.auth_cache import AuthCache
from utils.jwks import get_public_key

logger = logging.getLogger(__name__)


async def _ensure_fresh_access_token(
    username: str,
    auth_cache: AuthCache
) -> dict:
    auth = await auth_cache.get(username)
    if not auth:
        raise ValueError(f"No cached auth for user {username}")

    try:
        jwt.decode(
            auth['access_token'],
            options={"verify_signature": False, "verify_exp": True}
        )
    except jwt.ExpiredSignatureError:
        logger.info("Access token expired for %s, refreshing", username)
        auth = await refresh_access_token(username, auth_cache)

    return auth


async def refresh_access_token(
    username: str,
    auth_cache: AuthCache
) -> dict:
    auth = await auth_cache.get(username)
    if not auth:
        raise ValueError(f"No cached auth for user {username}")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f'{config.AUTH_URL}/token',
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

    if 'id_token' in data:
        decoded = jwt.decode(
            data['id_token'],
            await get_public_key(),
            algorithms=['EdDSA'],
            audience=config.CLIENT_ID
        )
        auth['expires_at'] = datetime.fromtimestamp(decoded['exp'], tz=timezone.utc)

    await auth_cache.upsert(auth)
    return auth


async def mcp_token_exchange(
    username: str,
    auth_cache: AuthCache
) -> dict:
    auth = await _ensure_fresh_access_token(username, auth_cache)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{config.AUTH_URL}/token",
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
        raise RuntimeError(f"MCP token exchange failed: {response.status_code} {response.text}")

    data = response.json()
    auth['mcp_token'] = data['access_token']
    await auth_cache.upsert(auth)
    return auth


async def get_external_token(
    username: str,
    auth_cache: AuthCache,
    provider_id: str
) -> dict:
    auth = await _ensure_fresh_access_token(username, auth_cache)

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{config.AUTH_URL}/federation/tokens",
            headers={
                "Authorization": f"Bearer {auth['access_token']}"
            },
            params={
                "provider_id": provider_id
            }
        )

    if not response.is_success:
        raise RuntimeError(f"External token fetch failed: {response.status_code} {response.text}")

    data = response.json()

    if auth.get('external_tokens') is None:
        auth['external_tokens'] = {}
    auth['external_tokens'][provider_id] = data
    await auth_cache.upsert(auth)
    return data
