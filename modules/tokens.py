import logging

import httpx
import jwt

from config import config
from db.auth_cache import AuthCache
from utils.external_tokens import find_token

logger = logging.getLogger(__name__)


async def _ensure_fresh_access_token(
    user_id: int,
    auth_cache: AuthCache
) -> dict:
    auth = await auth_cache.get(user_id)
    if not auth:
        raise ValueError(f"No cached auth for user {user_id}")

    try:
        jwt.decode(
            auth['access_token'],
            options={"verify_signature": False, "verify_exp": True}
        )
    except jwt.ExpiredSignatureError:
        logger.info("Access token expired for %s, refreshing", auth['email'])
        auth = await refresh_access_token(user_id, auth_cache)

    return auth


async def refresh_access_token(
    user_id: int,
    auth_cache: AuthCache
) -> dict:
    auth = await auth_cache.get(user_id)
    if not auth:
        raise ValueError(f"No cached auth for user {user_id}")

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

    await auth_cache.upsert(auth)
    return auth


async def mcp_token_exchange(
    user_id: int,
    auth_cache: AuthCache
) -> dict:
    auth = await _ensure_fresh_access_token(user_id, auth_cache)

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
    user_id: int,
    auth_cache: AuthCache,
    provider_id: str,
    subject: str
) -> dict:
    auth = await _ensure_fresh_access_token(user_id, auth_cache)

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{config.AUTH_URL}/federation/tokens",
            headers={
                "Authorization": f"Bearer {auth['access_token']}"
            },
            params={
                "provider_id": provider_id,
                "subject": subject
            }
        )

    if not response.is_success:
        raise RuntimeError(f"External token fetch failed: {response.status_code} {response.text}")

    data = response.json()

    data['subject'] = subject

    if auth.get('external_tokens') is None:
        auth['external_tokens'] = []

    existing = find_token(auth['external_tokens'], provider_id, subject)
    if existing:
        existing.update(data)
    else:
        auth['external_tokens'].append(data)

    await auth_cache.upsert(auth)
    return data
