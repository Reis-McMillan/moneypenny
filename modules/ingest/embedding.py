import re


BOILERPLATE_PATTERNS = re.compile(
    r'(?i)('
    r'unsubscribe|view\s+in\s+browser|privacy\s+policy|terms\s+of\s+service'
    r'|manage\s+preferences|email\s+preferences|opt[\s-]?out'
    r'|do\s+not\s+reply|no[\s-]?reply|add\s+us\s+to\s+your\s+address\s+book'
    r'|this\s+email\s+was\s+sent\s+to|you\s+are\s+receiving\s+this'
    r'|to\s+stop\s+receiving|update\s+your\s+preferences'
    r')'
)

INVISIBLE_CHARS = re.compile('[​‌‍­͏﻿]+')
PIPE_TABLE_LINE = re.compile(r'^\s*\|[\s\-|]*\|?\s*$')
EXCESSIVE_WHITESPACE = re.compile(r'\n{3,}')

MAX_EMBED_CHARS = 4000


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
    return clean_body(email.get('body', ''))


def build_metadata_text(email: dict) -> str:
    return f"From: {email.get('from', '')}\nSubject: {email.get('subject', '')}"
