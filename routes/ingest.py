from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from middleware.authenticated import User
from modules.ingest import scheduler


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
            'token_id': token['token_id'],
            'provider_id': token['provider_id'],
            'subject': token['subject'],
            'count': await email_db.count(
                owner, provider_id=token['provider_id'], account_subject=token['subject']
            ),
        })

    return JSONResponse({'total': total, 'accounts': accounts})


def _read_status(user_id: int, token_id: int) -> dict:
    redis = scheduler.app.backend.client
    raw = redis.hgetall(f"ingest:status:{user_id}:{token_id}") or {}
    return {
        (k.decode() if isinstance(k, bytes) else k):
        (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw.items()
    }


async def get_status(request: Request):
    user_id = _resolve_target_user_id(request)
    token_ids = scheduler.list_schedules(user_id)

    auth = await request.app.state.db.auth_cache.get(user_id)
    by_id = {t['token_id']: t for t in ((auth or {}).get('external_tokens') or [])}

    out = []
    for token_id in token_ids:
        token = by_id.get(token_id)
        if not token:
            continue
        status = _read_status(user_id, token_id)
        out.append({
            'token_id': token_id,
            'provider_id': token['provider_id'],
            'subject': token['subject'],
            'currently_ingesting': status.get('currently_ingesting') == '1',
            'last_run_at': status.get('last_run_at') or None,
            'last_error': status.get('last_error') or None,
            'last_error_at': status.get('last_error_at') or None,
        })

    return JSONResponse({'services': out})
