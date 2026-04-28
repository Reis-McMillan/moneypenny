from abc import ABC, abstractmethod
import asyncio
from base64 import urlsafe_b64decode
from datetime import datetime, timezone
import logging

import trafilatura

from config import config
from db.auth_cache import AuthCache
from db.email import Email
from modules.tokens import VerysClient

logger = logging.getLogger(__name__)


def _find_token(tokens: list[dict], provider_id: str, subject: str) -> dict | None:
    for t in tokens:
        if t.get('provider_id') == provider_id and t.get('subject') == subject:
            return t
    return None


class Service(ABC):
    HEADER_MAP = {
        'Subject': 'subject',
        'From': 'from',
        'To': 'to',
        'Date': 'date',
        'Reply-To': 'reply_to',
        'Message-Id': 'message_id',
        'Sender': 'sender',
        'List-Unsubscribe': 'list_unsubscribe',
        'Delivered-To': 'delivered_to',
        'Content-Type': 'content_type',
    }

    provider_id: str
    queue: asyncio.Queue
    auth_cache: AuthCache
    email_db: Email
    verys_client: VerysClient
    _services: dict
    _registry: dict[str, type['Service']] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, 'provider_id') and isinstance(cls.provider_id, str):
            Service._registry[cls.provider_id] = cls

    def __init__(self, user_id: int, subject: str):
        self.user_id = user_id
        self.subject = subject
        self.task: asyncio.Task | None = None
        self.currently_ingesting: bool = False
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.last_error_at: datetime | None = None

    @classmethod
    def for_provider(cls, provider_id: str) -> type['Service'] | None:
        return cls._registry.get(provider_id)

    @classmethod
    def set_queue(cls, queue):
        cls.queue = queue

    @classmethod
    def set_auth_cache(cls, auth_cache: AuthCache):
        cls.auth_cache = auth_cache

    @classmethod
    def set_email_db(cls, email_db: Email):
        cls.email_db = email_db

    @classmethod
    def set_services(cls, services: dict):
        cls._services = services

    @classmethod
    def set_verys_client(cls, verys_client: VerysClient):
        cls.verys_client = verys_client

    async def get_token(self) -> str:
        auth = await self.auth_cache.get(self.user_id)
        if not auth:
            raise ValueError(f"No cached auth for user {self.user_id}")

        token = _find_token(
            auth.get('external_tokens') or [],
            self.provider_id, self.subject
        )

        if token:
            expires_at = token.get('expires_at')
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if not expires_at or expires_at > datetime.now(timezone.utc):
                return token['access_token']
            auth = await self.verys_client.get_external_tokens(
                auth, token_id=token['token_id']
            )
        else:
            auth = await self.verys_client.get_external_tokens(auth)

        token = _find_token(
            auth.get('external_tokens') or [],
            self.provider_id, self.subject
        )
        if not token:
            raise ValueError(
                f"No external token for {self.provider_id}/{self.subject}"
            )
        return token['access_token']

    @staticmethod
    def _decode_part(parts: list[dict], mime_type: str) -> str | None:
        for part in parts:
            if part.get('mimeType') == mime_type:
                data = part.get('body', {}).get('data', '')
                if data:
                    return urlsafe_b64decode(data).decode('utf-8', errors='replace')
        return None

    def _headers_to_dict(self, headers: list[dict]) -> dict:
        result = {}
        for h in headers:
            key = h.get('name', '')
            if key in self.HEADER_MAP:
                result[self.HEADER_MAP[key]] = h.get('value', '')
        return result

    def _decode_body(self, payload: dict) -> str:
        parts = payload.get('parts', [])

        html = self._decode_part(parts, 'text/html')
        if html:
            extracted = trafilatura.extract(html)
            if extracted:
                return extracted

        plain = self._decode_part(parts, 'text/plain')
        if plain:
            return plain

        body_data = payload.get('body', {}).get('data', '')
        if body_data:
            raw = urlsafe_b64decode(body_data).decode('utf-8', errors='replace')
            if raw.strip().startswith('<'):
                extracted = trafilatura.extract(raw)
                if extracted:
                    return extracted
            return raw
        return ''

    def normalize_email(self, raw: dict, owner: str) -> dict:
        headers = self._headers_to_dict(raw.get('payload', {}).get('headers', []))
        body = self._decode_body(raw.get('payload', {}))

        doc = {
            'id': raw['id'],
            'owner': owner,
            'provider_id': self.provider_id,
            'account_subject': self.subject,
            'subject': headers.get('subject', ''),
            'from': headers.get('from', ''),
            'body': body,
            'ingested_at': datetime.now(timezone.utc),
        }

        if 'threadId' in raw:
            doc['threadId'] = raw['threadId']
        if 'labelIds' in raw:
            doc['labelIds'] = raw['labelIds']

        for field in ('to', 'date', 'reply_to', 'message_id', 'sender',
                    'list_unsubscribe', 'delivered_to', 'content_type'):
            if field in headers:
                doc[field] = headers[field]

        return doc

    @abstractmethod
    async def queue_emails(self, last_dt: datetime, owner: str):
        pass

    async def run(self):
        key = (self.user_id, self.provider_id)
        while True:
            auth = await self.auth_cache.get(self.user_id)
            if not auth:
                break

            token = _find_token(
                auth.get('external_tokens') or [],
                self.provider_id, self.subject
            )
            if not token:
                break

            self.currently_ingesting = True
            try:
                last_dt = await self.email_db.get_last_dt(auth['email'])
                await self.queue_emails(last_dt, auth['email'])
                self.last_run_at = datetime.now(timezone.utc)
                self.last_error = None
                self.last_error_at = None
            except Exception as e:
                self.last_error = repr(e)
                self.last_error_at = datetime.now(timezone.utc)
                logger.exception(
                    "Error in service %s for user %s",
                    self.provider_id, self.user_id
                )
            finally:
                self.currently_ingesting = False

            await asyncio.sleep(config.EMAIL_CHECK_INTERVAL)

        self._services.pop(key, None)
        logger.info("Service %s stopped for user %s", self.provider_id, self.user_id)

    def start(self):
        key = (self.user_id, self.provider_id)
        self.task = asyncio.create_task(self.run())
        self._services[key] = self
        return self

    def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
        key = (self.user_id, self.provider_id)
        self._services.pop(key, None)
