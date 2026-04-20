import logging

import httpx
import jwt

from config import config
from db.auth_cache import AuthCache
from utils.external_tokens import find_token

logger = logging.getLogger(__name__)

async def mcp_auth():
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{config.MCP_URL}/auth/initialize"
        )
    
    redirect_url = response.headers.get('Location')
    if not redirect_url:
        raise RuntimeError('MCP authentication failed.')
    return redirect_url


def token_expired(token: str | bytes):
    try:
        jwt.decode(
            token,
            options={"verify_signature": False, "verify_exp": True}
        )
    except jwt.ExpiredSignatureError:
        return True


async def _ensure_fresh_access_token(
    auth: dict
) -> dict:
    if token_expired(auth['access_token']):
        logger.info("Access token expired for %s, refreshing", auth['email'])
        auth = await refresh_access_token(auth)

    return auth


async def refresh_access_token(
    auth: dict
) -> dict:
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

    return auth


async def authorize_mcp():
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{config.MCP_URL}/auth/initialize")
    
    if not response.status_code == 302:
        error_msg = "Unable to fetch MCP authorization initialization at %s".format(
            "{config.MCP_URL}/auth/initialize"
        )
        return RuntimeError(error_msg)
    
    return response.headers['Location']


async def mcp_token_exchange(
    auth: dict
) -> dict:
    auth = await _ensure_fresh_access_token(auth)
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
        logger.warning(
            f"MCP token exchange failed: {response.status_code} {response.text}"
        )
        return auth

    data = response.json()
    auth['mcp_token'] = data['access_token']
    return auth


async def get_external_token(
    auth: dict,
    provider_id: str,
    subject: str
) -> dict:
    auth = await _ensure_fresh_access_token(auth)

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

    auth
