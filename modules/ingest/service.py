from abc import ABC, abstractmethod
from base64 import urlsafe_b64decode
from datetime import datetime, timezone
import logging
from typing import Callable
import trafilatura

from modules.tokens import SyncVerysClient


logger = logging.getLogger(__name__)


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
    _registry: dict[str, type['Service']] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, 'provider_id') and isinstance(cls.provider_id, str):
            Service._registry[cls.provider_id] = cls

    def __init__(self, auth: dict, token_id: int, verys_client: SyncVerysClient):
        self.auth = auth
        self.token_id = token_id
        self.verys_client = verys_client
        self.user_id = auth['user_id']
        self.external_token = self.verys_client.find_token(
            auth['external_tokens'], self.token_id
        )

    @classmethod
    def for_provider(cls, provider_id: str) -> type['Service'] | None:
        return cls._registry.get(provider_id)

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

    def normalize_email(self, raw: dict) -> dict:
        headers = self._headers_to_dict(raw.get('payload', {}).get('headers', []))
        body = self._decode_body(raw.get('payload', {}))

        doc = {
            'id': raw['id'],
            'owner': self.user_id,
            'provider_id': self.provider_id,
            'account_subject': self.external_token['subject'],
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
    def _fetch(
        self,
        last_dt
    ):
        ...

    def fetch_mail(
        self,
        last_dt: datetime,
        dispatch: Callable,
    ) -> None:
        if (self.external_token['expires_at'] is None or
            self.external_token['expires_at'] <= datetime.now(timezone.utc)):
            try:
                self.auth = self.verys_client.get_external_tokens(self.auth, self.token_id)
            except RuntimeError as e:
                if not "External token fetch failed" in str(e):
                    raise e
                return
            self.external_token = self.verys_client.find_token(self.auth['external_tokens'], self.token_id)
        
        for email in self._fetch(last_dt):
            email = self.normalize_email(email)
            dispatch(email)
