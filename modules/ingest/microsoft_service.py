import logging
from datetime import datetime, timezone
from typing import Generator

import httpx
import trafilatura

from modules.ingest.service import Service

logger = logging.getLogger(__name__)

GRAPH_API = 'https://graph.microsoft.com/v1.0'


class MicrosoftService(Service):
    provider_id = 'microsoft'

    @staticmethod
    def _decode_body(body: dict) -> str:
        content_type = body.get('contentType')
        content = body.get('content', '') or ''
        if content_type == 'html':
            extracted = trafilatura.extract(content)
            return extracted or content
        if content_type == 'text':
            return content
        return ''

    @staticmethod
    def _addr(obj: dict | None) -> str:
        return (obj or {}).get('emailAddress', {}).get('address', '')

    @classmethod
    def _join_addrs(cls, items: list[dict] | None) -> str:
        return ', '.join(filter(None, (cls._addr(i) for i in items or [])))

    def _list_messages(
        self, client: httpx.Client, last_dt: datetime,
    ) -> Generator[dict, None, None]:
        url = f'{GRAPH_API}/me/messages'
        params: dict | None = {'$orderby': 'receivedDateTime asc'}
        if last_dt.year > 1970:
            params['$filter'] = f"receivedDateTime ge {last_dt.isoformat()}"

        while url:
            response = client.get(
                url,
                headers={'Authorization': f'Bearer {self.external_token['access_token']}'},
                params=params,
            )
            if not response.is_success:
                logger.warning(
                    "Microsoft list failed: %s %s",
                    response.status_code, response.text,
                )
                return

            data = response.json()
            for msg in data.get('value', []):
                yield msg

            # nextLink is a full URL with all query params baked in
            url = data.get('@odata.nextLink')
            params = None

    def _fetch(self, last_dt: datetime) -> Generator[dict, None, None]:
        with httpx.Client() as client:
            yield from self._list_messages(client, last_dt)

    def normalize_email(self, raw: dict) -> dict:
        body = self._decode_body(raw.get('body') or {})

        doc = {
            'id': raw['id'],
            'owner': self.user_id,
            'provider_id': self.provider_id,
            'account_subject': self.external_token['subject'],
            'subject': raw.get('subject', '') or '',
            'from': self._addr(raw.get('from')),
            'body': body,
            'ingested_at': datetime.now(timezone.utc),
        }

        if conv := raw.get('conversationId'):
            doc['threadId'] = conv
        if to_str := self._join_addrs(raw.get('toRecipients')):
            doc['to'] = to_str
        if dt := raw.get('receivedDateTime'):
            doc['date'] = dt
        if reply := self._join_addrs(raw.get('replyTo')):
            doc['reply_to'] = reply
        if mid := raw.get('internetMessageId'):
            doc['message_id'] = mid
        if sender_addr := self._addr(raw.get('sender')):
            doc['sender'] = sender_addr

        return doc
