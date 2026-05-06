from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from starlette.exceptions import HTTPException
from voluptuous.error import Invalid
from json import JSONDecodeError

from db.action import Action
from middleware.authenticated import User
from modules.tokens import build_reauth_url


async def get_actions(request: Request):
    user: User = request.user

    action_db: Action = request.app.state.db.action
    user_actions = await action_db.get(user.user_id)

    return JSONResponse({'actions': user_actions})


async def create_action(request: Request):
    user: User = request.user
    action_db: Action = request.app.state.db.action

    try:
        body = await request.json()
    except JSONDecodeError:
        raise HTTPException(status_code=400, detail="Could not parse request body to JSON.")

    provider_id = body.get('provider_id')
    if not provider_id:
        raise HTTPException(status_code=400, detail="provider_id is required.")

    token = next(
        (t for t in (user.external_tokens or []) if t['provider_id'] == provider_id),
        None,
    )
    if not token:
        raise HTTPException(status_code=404, detail="No linked account for that provider_id.")

    try:
        await action_db.upsert({
            'user_id': user.user_id,
            'token_id': token['token_id'],
            'token_email': token['email'],
            'reauth_url': build_reauth_url(provider_id),
        })
    except Invalid as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to upsert new action: {str(e)}"
        )

    return Response(status_code=201)
