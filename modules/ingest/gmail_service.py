import logging
from datetime import datetime

import httpx

from modules.ingest.service import Service

logger = logging.getLogger(__name__)

GMAIL_API = 'https://gmail.googleapis.com/gmail/v1'


class GmailService(Service):
    provider_id = 'google'

    async def _list_messages(
        self, client: httpx.AsyncClient, query: str, token: str
    ) -> list[dict]:
        messages = []
        params = {'q': query}

        while True:
            response = await client.get(
                f'{GMAIL_API}/users/me/messages',
                headers={'Authorization': f'Bearer {token}'},
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

    async def _get_message(
        self, client: httpx.AsyncClient, msg_id: str, token: str
    ) -> dict | None:
        response = await client.get(
            f'{GMAIL_API}/users/me/messages/{msg_id}',
            headers={'Authorization': f'Bearer {token}'}
        )
        if not response.is_success:
            logger.warning(
                "Gmail get message %s failed: %s",
                msg_id, response.status_code
            )
            return None
        return response.json()

    async def queue_emails(self, last_dt: datetime, owner: str):
        token = await self.get_token()
        query = f"after:{int(last_dt.timestamp())}"

        async with httpx.AsyncClient() as client:
            messages = await self._list_messages(client, query, token)
            for msg in messages:
                if await self.email_db.exists(msg['id']):
                    continue
                full = await self._get_message(client, msg['id'], token)
                if full:
                    await self.queue.put(self.normalize_email(full, owner))
