from typing import Any
import os
import asyncio
import logging
from base64 import urlsafe_b64decode

import trafilatura

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from ollama import AsyncClient

import re

import config
from db.email import Email


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


BOILERPLATE_PATTERNS = re.compile(
    r'(?i)('
    r'unsubscribe|view\s+in\s+browser|privacy\s+policy|terms\s+of\s+service'
    r'|manage\s+preferences|email\s+preferences|opt[\s-]?out'
    r'|do\s+not\s+reply|no[\s-]?reply|add\s+us\s+to\s+your\s+address\s+book'
    r'|this\s+email\s+was\s+sent\s+to|you\s+are\s+receiving\s+this'
    r'|to\s+stop\s+receiving|update\s+your\s+preferences'
    r')'
)

INVISIBLE_CHARS = re.compile(r'[\u200b\u200c\u200d\u00ad\u034f\ufeff]+')
PIPE_TABLE_LINE = re.compile(r'^\s*\|[\s\-|]*\|?\s*$')
EXCESSIVE_WHITESPACE = re.compile(r'\n{3,}')


def clean_body(text: str) -> str:
    text = INVISIBLE_CHARS.sub('', text)
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        if PIPE_TABLE_LINE.match(line):
            continue
        if BOILERPLATE_PATTERNS.search(line):
            continue
        stripped = line.strip()
        if stripped:
            cleaned.append(stripped)
    text = '\n'.join(cleaned)
    text = EXCESSIVE_WHITESPACE.sub('\n\n', text)
    return text.strip()


def build_embed_text(email: dict) -> str:
    body = clean_body(email.get('body', ''))
    return f"From: {email.get('from', '')}\nSubject: {email.get('subject', '')}\nBody: {body}"


def build_metadata_text(email: dict) -> str:
    return f"From: {email.get('from', '')}\nSubject: {email.get('subject', '')}"


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


def _headers_to_dict(headers: list[dict]) -> dict:
    result = {}
    for h in headers:
        key = h.get('name', '')
        if key in HEADER_MAP:
            result[HEADER_MAP[key]] = h.get('value', '')
    return result


def _decode_part(parts: list[dict], mime_type: str) -> str | None:
    for part in parts:
        if part.get('mimeType') == mime_type:
            data = part.get('body', {}).get('data', '')
            if data:
                return urlsafe_b64decode(data).decode('utf-8', errors='replace')
    return None


def _decode_body(payload: dict) -> str:
    parts = payload.get('parts', [])

    html = _decode_part(parts, 'text/html')
    if html:
        extracted = trafilatura.extract(html)
        if extracted:
            return extracted

    plain = _decode_part(parts, 'text/plain')
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


def normalize_email(raw: dict) -> dict:
    headers = _headers_to_dict(raw.get('payload', {}).get('headers', []))
    body = _decode_body(raw.get('payload', {}))

    doc = {
        'id': raw['id'],
        'subject': headers.get('subject', ''),
        'from': headers.get('from', ''),
        'body': body,
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

class GmailService:
    def __init__(self,
                 creds_file_path: str,
                 token_path: str,
                 queue: asyncio.Queue,
                 scopes: list[str] = ['https://www.googleapis.com/auth/gmail.modify']):
        logger.info(f"Initializing GmailService with creds file: {creds_file_path}")
        self.creds_file_path = creds_file_path
        self.queue = queue
        self.email_db = Email()
        self.token_path = token_path
        self.scopes = scopes
        self.token = self._get_token()
        logger.info("Token retrieved successfully")
        self.service = self._get_service()
        logger.info("Gmail service initialized")
        self.user_email = self._get_user_email()
        logger.info(f"User email retrieved: {self.user_email}")

    def _get_token(self) -> Credentials:
        """Get or refresh Google API token"""

        token = None

        if os.path.exists(self.token_path):
            logger.info('Loading token from file')
            token = Credentials.from_authorized_user_file(self.token_path, self.scopes)

        if not token or not token.valid:
            if token and token.expired and token.refresh_token:
                logger.info('Refreshing token')
                token.refresh(Request())
            else:
                logger.info('Fetching new token')
                flow = InstalledAppFlow.from_client_secrets_file(self.creds_file_path, self.scopes)
                token = flow.run_local_server(port=0)

            with open(self.token_path, 'w') as token_file:
                token_file.write(token.to_json())
                logger.info(f'Token saved to {self.token_path}')

        return token

    def _get_service(self) -> Any:
        """Initialize Gmail API service"""
        try:
            service = build('gmail', 'v1', credentials=self.token)
            return service
        except HttpError as error:
            logger.error(f'An error occurred building Gmail service: {error}')
            raise ValueError(f'An error occurred: {error}')

    def _get_user_email(self) -> str:
        """Get user email address"""
        profile = self.service.users().getProfile(userId='me').execute()
        user_email = profile.get('emailAddress', '')
        return user_email

    async def queue_emails(self):
        resp = self.service.users().messages().list(userId='me').execute()
        if resp.get('messages'):
            for msg in resp['messages']:
                if await self.email_db.exists(msg['id']):
                    continue
                full = self.service.users().messages().get(userId='me', id=msg['id']).execute()
                await self.queue.put(normalize_email(full))
        while resp.get('nextPageToken'):
            pageToken = resp['nextPageToken']
            resp = self.service.users().messages().list(userId='me', pageToken=pageToken).execute()
            if resp.get('messages'):
                for msg in resp['messages']:
                    if await self.email_db.exists(msg['id']):
                        continue
                    full = self.service.users().messages().get(userId='me', id=msg['id']).execute()
                    await self.queue.put(normalize_email(full))

class Embedder:

    EMBEDDING_MODEL = "nomic-embed-text"

    def __init__(self, queue: asyncio.Queue):
        self.queue = queue
        self.ollama_client = AsyncClient(host="http://localhost:11434")
        self.email_db = Email()

    async def start(self):
        await self.email_db.ensure_search_index()
        logger.info("Embedder started, processing queue")
        while True:
            email = await self.queue.get()
            try:
                await self._process_email(email)
            except Exception as e:
                logger.error(f"Failed to process email: {e}")
            finally:
                self.queue.task_done()

    MAX_EMBED_CHARS = 4000

    async def _embed(self, text: str) -> list[float]:
        text = f"search_document: {text}"
        response = await self.ollama_client.embed(model=self.EMBEDDING_MODEL, input=text[:self.MAX_EMBED_CHARS])
        return response.embeddings[0]

    async def _process_email(self, email: dict):
        email['embedding'] = await self._embed(build_embed_text(email))
        email['metadata_embedding'] = await self._embed(build_metadata_text(email))

        upserted = await self.email_db.upsert(email)
        logger.info(f"{'Inserted' if upserted else 'Updated'} email '{email.get('subject', '')}'")


async def run_ingest():
    queue = asyncio.Queue()
    gmail = GmailService(
        creds_file_path=config.CREDS_PATH,
        token_path=config.TOKEN_PATH,
        queue=queue,
    )
    embedder = Embedder(queue)

    embed_task = asyncio.create_task(embedder.start())
    await gmail.queue_emails()
    await queue.join()

    embed_task.cancel()
    logger.info("Ingestion complete")


async def run_reembed():
    email_db = Email()
    ollama_client = AsyncClient(host="http://localhost:11434")
    emails = await email_db.get_all()
    logger.info(f"Re-embedding {len(emails)} emails")

    for i, email in enumerate(emails, 1):
        embed_text = f"search_document: {build_embed_text(email)}"[:Embedder.MAX_EMBED_CHARS]
        meta_text = f"search_document: {build_metadata_text(email)}"[:Embedder.MAX_EMBED_CHARS]

        embed_resp = await ollama_client.embed(model=Embedder.EMBEDDING_MODEL, input=embed_text)
        meta_resp = await ollama_client.embed(model=Embedder.EMBEDDING_MODEL, input=meta_text)

        await email_db.collection.update_one(
            {'id': email['id']},
            {'$set': {
                'embedding': embed_resp.embeddings[0],
                'metadata_embedding': meta_resp.embeddings[0],
            }},
        )
        logger.info(f"[{i}/{len(emails)}] Re-embedded '{email.get('subject', '')}'")

    logger.info("Re-embedding complete")


if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == '--reembed':
        asyncio.run(run_reembed())
    else:
        asyncio.run(run_ingest())
