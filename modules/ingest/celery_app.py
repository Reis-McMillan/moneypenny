from celery import Celery

from config import config


app = Celery(
    "moneypenny",
    broker=config.REDIS_URL,
    backend=config.REDIS_URL,
    include=["modules.ingest.tasks"],
)
app.conf.task_routes = {
    "fetch_emails": {"queue": "fetch"},
    "embed_email":  {"queue": "embed"},
}
app.conf.task_acks_late = True
app.conf.worker_prefetch_multiplier = 1
app.conf.beat_scheduler = "redbeat.RedBeatScheduler"
app.conf.redbeat_redis_url = config.REDIS_URL
app.conf.redbeat_lock_timeout = 3600
