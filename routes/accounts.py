import logging
from urllib.parse import urlencode

import httpx
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Router, Route

from config import config
from db.auth_cache import AuthCache
from middleware.authenticated import User
from modules.tokens import token_expired, refresh_access_token
from modules.ingest.service import Service
from utils.external_tokens import find_token

logger = logging.getLogger(__name__)


async def get_linked_accounts(request: Request):
    auth_cache: AuthCache = request.app.state.db.auth_cache
    user: User = request.user
    
    if token_expired(user.access_token):
        auth = await auth_cache.get(user.user_id)
        auth = await refresh_access_token(auth)
        user.access_token = auth['access_token']
        user.external_tokens = auth.get('external_tokens')
    
    headers = {'Authorization': f"Bearer {user.access_token}"}

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f'{config.AUTH_URL}/federation/providers',
            headers=headers
        )

        if not response.is_success:
            raise HTTPException(status_code=502, detail='Failed to list providers.')

        providers = response.json()

        cached = user.external_tokens or []
        new_tokens: list[dict] = []
        changed = False

        for entry in providers:
            provider_id = entry['provider_id']
            subject = entry['subject']
            existing = find_token(cached, provider_id, subject)

            if existing:
                new_tokens.append(existing)
                continue

            token_response = await client.get(
                f'{config.AUTH_URL}/federation/tokens',
                headers=headers,
                params={'provider_id': provider_id, 'subject': subject}
            )

            if not token_response.is_success:
                logger.warning(
                    "Failed to fetch token for provider %s: %s %s",
                    provider_id, token_response.status_code, token_response.text
                )
                continue

            token_data = token_response.json()
            token_data['subject'] = subject
            new_tokens.append(token_data)
            changed = True

        if len(new_tokens) != len(cached):
            changed = True

    if changed:
        auth = await auth_cache.get(user.user_id)
        auth['external_tokens'] = new_tokens or None
        await auth_cache.upsert(auth)

    services = request.app.state.services
    provider_ids_now = {entry['provider_id'] for entry in providers}

    for key in list(services):
        uid, pid = key
        if uid == user.user_id and pid not in provider_ids_now:
            services[key].stop()

    for entry in providers:
        key = (user.user_id, entry['provider_id'])
        if key not in services:
            svc_cls = Service.for_provider(entry['provider_id'])
            if svc_cls:
                svc_cls(user.user_id, entry['subject']).start()

    return JSONResponse(content=providers)


async def add_linked_account(request: Request):
    provider_id = request.query_params.get('provider_id')
    if not provider_id:
        raise HTTPException(status_code=400, detail='provider_id is required.')

    redirect_url = (
        f'{config.AUTH_URL}/federation/initiate?'
        f'{urlencode({"provider_id": provider_id})}'
    )

    return JSONResponse(content={'redirect_url': redirect_url})