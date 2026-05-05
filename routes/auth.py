from datetime import datetime, timedelta, timezone
import logging
import httpx
import jwt
import secrets
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

from config import config
from modules.ingest import scheduler
from modules.tokens import VerysClient
from utils.jwks import get_public_key


def create_auth_url():
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    query_params = {
        'response_type': 'code',
        'client_id': config.CLIENT_ID,
        'redirect_uri': config.REDIRECT_URI,
        'scope': config.SCOPES,
        'state': state,
        'nonce': nonce
    }

    url = f'{config.AUTH_URL}/authorize?{urlencode(query_params)}'

    return url, state, nonce


async def initialize(request: Request):
    auth_url, state, nonce = create_auth_url()
    
    return_url = request.query_params.get('return_url')
    await request.app.state.db.authorization.upsert({
        'state': state,
        'nonce': nonce,
        'return_url': return_url
    })

    return RedirectResponse(
        url = auth_url,
        status_code=302
    )


async def callback(request: Request):
    error = request.query_params.get('error')
    if error:
        raise HTTPException(status_code=400, detail="Login failed.")
    code = request.query_params.get('code')
    if not code:
        raise HTTPException(status_code=400, detail="Authorization code required.")
    state = request.query_params.get('state')
    if not state:
        raise HTTPException(status_code=400, detail="State parameter required.")

    authorization = await request.app.state.db.authorization.get(state)
    if not authorization or authorization['state'] != state:
        raise HTTPException(status_code=400, detail='State mismatch.')

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{config.AUTH_URL}/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.REDIRECT_URI,
                "client_id": config.CLIENT_ID,
                "client_secret": config.CLIENT_SECRET
            }
        )

    if not response.is_success:
        raise HTTPException(status_code=500, detail='Failed to retrieve tokens.')

    tokens = response.json()
    id_token = tokens['id_token']
    try:
        decoded = jwt.decode(
            id_token,
            await get_public_key(),
            algorithms=['EdDSA'],
            audience=config.CLIENT_ID
        )
    except jwt.InvalidTokenError:
        raise HTTPException(400, detail='Invalid identity token.')

    if decoded.get('nonce') != authorization['nonce']:
        raise HTTPException(status_code=400, detail='Nonce missing or nonce mismatch.')

    return_url = authorization['return_url']
    await request.app.state.db.authorization.delete(state)

    verys_client : VerysClient = request.app.state.verys_client
    auth = {
        'user_id': int(decoded['sub']),
        'email': decoded['email'],
        'roles': decoded['roles'],
        'access_token': tokens['access_token'],
        'refresh_token': tokens['refresh_token'],
        'expires_at': (
            datetime.fromtimestamp(
                decoded['auth_time'], tz=timezone.utc
            ) + timedelta(days=60)),
        'mcp_token': None,
        'external_tokens': None
    }
    auth = await verys_client.mcp_token_exchange(auth)
    auth = await verys_client.get_external_tokens(auth)
    scheduler.update_tasks(auth)

    if return_url:
        return RedirectResponse(
            url=return_url,
            status_code=302
        )
    
    return JSONResponse(
        content={
            'message': 'Successfully exchanged code for tokens.'
        }
    )