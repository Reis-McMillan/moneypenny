import logging
from urllib.parse import urlencode

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from config import config
from middleware.authenticated import User
from modules.tokens import VerysClient

logger = logging.getLogger(__name__)


async def get_linked_accounts(request: Request):
    user: User = request.user

    external_tokens = user.external_tokens or []
    return JSONResponse(
        content=external_tokens
    )


async def refresh_linked_accounts(request: Request):
    user: User = request.user

    verys_client: VerysClient = request.app.state.verys_client
    auth = await verys_client.get_external_tokens(user.auth)

    return JSONResponse(
        content=auth['external_tokens']
    )


async def add_linked_account(request: Request):
    provider_id = request.query_params.get('provider_id')
    if not provider_id:
        raise HTTPException(status_code=400, detail='provider_id is required.')

    redirect_url = (
        f'{config.AUTH_URL}/federation/initiate?'
        f'{urlencode({"provider_id": provider_id})}'
    )

    return JSONResponse(content={'redirect_url': redirect_url})