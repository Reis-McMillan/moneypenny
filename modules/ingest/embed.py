import asyncio
import logging

from openai import AsyncOpenAI

import re

from config import config
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
    return body


def build_metadata_text(email: dict) -> str:
    return f"From: {email.get('from', '')}\nSubject: {email.get('subject', '')}"


class Embedder:

    def __init__(self, queue: asyncio.Queue, email_db: Email):
        self.queue = queue
        self.client = AsyncOpenAI(base_url=config.VLLM_EMBED_URL, api_key="none")
        self.email_db = email_db
        self.task = None

    async def run(self):
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
        response = await self.client.embeddings.create(
            model=config.EMBEDDING_MODEL,
            input=text[:self.MAX_EMBED_CHARS]
        )
        return response.data[0].embedding

    async def _process_email(self, email: dict):
        email['embedding'] = await self._embed(build_embed_text(email))
        email['metadata_embedding'] = await self._embed(build_metadata_text(email))

        upserted = await self.email_db.upsert(email)
        logger.info(f"{'Inserted' if upserted else 'Updated'} email '{email.get('subject', '')}'")

    def start(self):
        self.task = asyncio.create_task(self.run())