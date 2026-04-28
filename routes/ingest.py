from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from middleware.authenticated import User


def _resolve_target_user_id(request: Request) -> int:
    user: User = request.user
    raw = request.query_params.get('user_id')
    if raw is None:
        return user.user_id
    try:
        target = int(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail='user_id must be an integer.')
    if target != user.user_id and not user.is_admin:
        raise HTTPException(status_code=403, detail='Admin role required.')
    return target


async def get_counts(request: Request):
    user_id = _resolve_target_user_id(request)
    auth_cache = request.app.state.db.auth_cache
    email_db = request.app.state.db.email

    auth = await auth_cache.get(user_id)
    if not auth:
        raise HTTPException(status_code=404, detail='User not found.')

    owner = auth['email']
    total = await email_db.count(owner)
    accounts = []
    for token in auth.get('external_tokens') or []:
        accounts.append({
            'provider_id': token['provider_id'],
            'subject': token['subject'],
            'count': await email_db.count(owner, provider_id=token['provider_id']),
        })

    return JSONResponse({'total': total, 'accounts': accounts})


async def get_status(request: Request):
    user_id = _resolve_target_user_id(request)
    services: dict = request.app.state.services

    out = []
    for (uid, provider_id), service in services.items():
        if uid != user_id:
            continue
        out.append({
            'provider_id': provider_id,
            'subject': service.subject,
            'currently_ingesting': service.currently_ingesting,
            'last_run_at': service.last_run_at.isoformat() if service.last_run_at else None,
            'last_error': service.last_error,
            'last_error_at': service.last_error_at.isoformat() if service.last_error_at else None,
            'task_alive': bool(service.task and not service.task.done()),
        })

    return JSONResponse({'services': out})
