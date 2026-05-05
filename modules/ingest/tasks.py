import logging
from datetime import datetime, timezone

from openai import OpenAI

from config import config
from db.action import SyncAction
from db.auth_cache import SyncAuthCache
from db.email import SyncEmail
from modules.ingest.celery import app
from modules.ingest.embedding import (
    MAX_EMBED_CHARS,
    build_embed_text,
    build_metadata_text,
)
from modules.ingest.service import Service
from modules.tokens import SyncVerysClient
import modules.ingest.gmail_service  # noqa: F401 — registers provider


logger = logging.getLogger(__name__)


_email = SyncEmail()
_auth_cache = SyncAuthCache()
_action = SyncAction()
_verys = SyncVerysClient(_auth_cache, _action)
_embed_client = OpenAI(base_url=config.VLLM_EMBED_URL, api_key="none")

def _status_key(user_id: int, token_id: int) -> str:
    return f"ingest:status:{user_id}:{token_id}"


def _set_status(user_id: int, token_id: int, **fields):
    redis = app.backend.client
    mapping = {k: ('' if v is None else str(v)) for k, v in fields.items()}
    redis.hset(_status_key(user_id, token_id), mapping=mapping)


@app.task(
    name="fetch_emails",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def fetch_emails(self, user_id: int, token_id: int):
    auth = _auth_cache.get(user_id)
    if not auth:
        return

    _set_status(user_id, token_id, currently_ingesting=1)
    try:
        token = _verys.find_token(auth['external_tokens'], token_id)
        if not token:
            logger.warning(
                "No external token user_id=%s token_id=%s; skipping fetch",
                user_id, token_id,
            )
            return

        provider_id = token['provider_id']
        svc_cls = Service.for_provider(provider_id)
        if svc_cls is None:
            logger.error("No Service registered for provider_id=%s", provider_id)
            return

        svc = svc_cls(auth, token_id, _verys)
        last_dt = _email.get_last_dt(
            auth['user_id'], token['provider_id'], token['subject']
        )

        def dispatch(email_doc: dict):
            email_doc['ingested_at'] = email_doc['ingested_at'].isoformat()
            embed_email.delay(email_doc)

        svc.fetch_mail(
            last_dt=last_dt,
            dispatch=dispatch,
        )

        _set_status(
            user_id, token_id,
            last_run_at=datetime.now(timezone.utc).isoformat(),
            last_error="",
            last_error_at="",
        )
    except Exception as e:
        _set_status(
            user_id, token_id,
            last_error=repr(e),
            last_error_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.exception(
            "fetch_emails failed user_id=%s token_id=%s", user_id, token_id,
        )
        raise
    finally:
        _set_status(user_id, token_id, currently_ingesting=0)


def _embed(text: str) -> list[float]:
    response = _embed_client.embeddings.create(
        model=config.EMBEDDING_MODEL,
        input=text[:MAX_EMBED_CHARS],
    )
    return response.data[0].embedding


@app.task(
    name="embed_email",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def embed_email(self, email_doc: dict):
    ingested_at = email_doc.get('ingested_at')
    if isinstance(ingested_at, str):
        email_doc['ingested_at'] = datetime.fromisoformat(ingested_at)

    email_doc['embedding'] = _embed(build_embed_text(email_doc))
    email_doc['metadata_embedding'] = _embed(build_metadata_text(email_doc))

    _email.upsert(email_doc)
    logger.info("Embedded email '%s'", email_doc.get('subject', ''))
