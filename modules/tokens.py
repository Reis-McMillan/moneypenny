import httpx

import config
from db.auth_cache import AuthCache


async def refresh_access_token(
    username: str,
    auth_cache: AuthCache
):
    auth = auth_cache.get(username)
    refresh_token = auth['refresh_token']

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f'{config.AUTH_URL}/token',
            data={
                "grant_type": "refresh_token",
                "client_id": config.CLIENT_ID,
                "client_secret": config.CLIENT_SECRET,
                "refresh_token": refresh_token
            }
        )

    if not response.ok:
        pass

    auth['access_token'] = response['access_token']
    # todo... check id token and update auth_cache expires at if necessary

async def mcp_token_exchange(
    username: str,
    auth_cache: AuthCache
):
    auth = auth_cache.get(username)
    access_token = auth_cache['access_token']

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{config.AUTH_URL}/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": config.CLIENT_ID,
                "client_secret": config.CLIENT_SECRET,
                "subject_token": access_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token"
            }
        )

    if not response.ok:
        # todo: handle failed response
        pass
    
    auth['mcp_token'] = response['access_token']
    await auth_cache.upsert(auth)


async def get_external_token(
    username: str,
    auth_cache: AuthCache,
    provider_id: str
):
    auth = auth_cache.get(username)
    access_token = auth_cache['access_token']

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{config.AUTH_URL}/federation/tokens",
            headers={
                "Authorizatoin": f"Bearer {access_token}"
            },
            params={
                "provider_id": provider_id
            }
        )