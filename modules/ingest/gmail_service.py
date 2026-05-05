import logging
from datetime import datetime
from typing import Generator

import httpx

from modules.ingest.service import Service

logger = logging.getLogger(__name__)

GMAIL_API = 'https://gmail.googleapis.com/gmail/v1'


class GmailService(Service):
    provider_id = 'google'

    def _list_messages(
        self, client: httpx.Client, query: str,
    ) -> list[dict]:
        messages = []
        params = {'q': query}

        while True:
            response = client.get(
                f'{GMAIL_API}/users/me/messages',
                headers={'Authorization': f'Bearer {self.external_token['access_token']}'},
                params=params
            )
            if not response.is_success:
                logger.warning(
                    "Gmail list failed: %s %s",
                    response.status_code, response.text
                )
                break

            data = response.json()
            messages.extend(data.get('messages', []))

            next_page = data.get('nextPageToken')
            if not next_page:
                break
            params['pageToken'] = next_page

        return messages

    def _get_message(
        self, client: httpx.Client, msg_id: str
    ) -> dict | None:
        response = client.get(
            f'{GMAIL_API}/users/me/messages/{msg_id}',
            headers={'Authorization': f'Bearer {self.external_token['access_token']}'}
        )
        if not response.is_success:
            logger.warning(
                "Gmail get message %s failed: %s",
                msg_id, response.status_code
            )
            return None
        return response.json()

    def _fetch(
        self,
        last_dt: datetime,
    ) -> Generator[dict, None, None]:
        query = f"after:{int(last_dt.timestamp())}"

        with httpx.Client() as client:
            messages = self._list_messages(client, query)
            for msg in messages:
                full = self._get_message(client, msg['id'])
                if full:
                    yield full
