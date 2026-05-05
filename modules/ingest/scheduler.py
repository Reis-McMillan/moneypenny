from datetime import timedelta

from celery.schedules import schedule
from redbeat import RedBeatSchedulerEntry

from config import config
from modules.ingest.celery import app


def _name(user_id: int, token_id: int) -> str:
    return f"ingest:{user_id}:{token_id}"


def _redbeat_key(user_id: int, token_id: int) -> str:
    return f"redbeat:{_name(user_id, token_id)}"


def upsert_schedule(
    user_id: int,
    token_id: int,
    interval_seconds: int = config.EMAIL_CHECK_INTERVAL,
):
    entry = RedBeatSchedulerEntry(
        name=_name(user_id, token_id),
        task="fetch_emails",
        schedule=schedule(run_every=timedelta(seconds=interval_seconds)),
        args=[user_id, token_id],
        app=app,
    )
    entry.save()


def remove_schedule(user_id: int, token_id: int):
    try:
        entry = RedBeatSchedulerEntry.from_key(
            _redbeat_key(user_id, token_id), app=app
        )
        entry.delete()
    except KeyError:
        pass


def list_schedules(user_id: int) -> set[int]:
    """Return the token_ids the user currently has scheduled."""
    out: set[int] = set()
    redis = app.backend.client
    prefix = f"redbeat:ingest:{user_id}:"
    for key in redis.scan_iter(match=f"{prefix}*"):
        key_str = key.decode() if isinstance(key, bytes) else key
        try:
            entry = RedBeatSchedulerEntry.from_key(key_str, app=app)
        except KeyError:
            continue
        args = entry.args or []
        if len(args) >= 2:
            out.add(args[1])
    return out


def update_tasks(auth: dict):
    user_id = auth['user_id']
    existing = list_schedules(user_id)
    current = {t['token_id'] for t in (auth.get('external_tokens') or [])}

    for token_id in current - existing:
        upsert_schedule(user_id, token_id)
    for token_id in existing - current:
        remove_schedule(user_id, token_id)
