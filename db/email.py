import logging
import re

from datetime import datetime

from pymongo.operations import SearchIndexModel
from voluptuous import Email as EmailValidator, Schema, Required, Optional, All, Coerce

from db.base import Base

logger = logging.getLogger(__name__)

class Email(Base):

    SEARCH_INDEX_NAME = "email_vector_index"
    METADATA_INDEX_NAME = "email_metadata_vector_index"

    def __init__(self):
        self.collection_name = 'emails'
        self.collection = self.client[self.db_name][self.collection_name]
        self.identity_fields = ['id', 'owner']

        self.schema = Schema({
            Required('id'): str,
            Required('owner'): EmailValidator(),
            Required('provider_id'): str,
            Required('account_subject'): str,
            Required('from'): EmailValidator(),
            Required('subject'): str,
            Required('body'): str,
            Required('embedding'): All(list, [Coerce(float)]),
            Required('metadata_embedding'): All(list, [Coerce(float)]),
            Required('ingested_at'): datetime,
            Optional('threadId'): str,
            Optional('labelIds'): [str],
            Optional('to'): str,
            Optional('date'): str,
            Optional('reply_to'): str,
            Optional('message_id'): str,
            Optional('sender'): str,
            Optional('list_unsubscribe'): str,
            Optional('delivered_to'): str,
            Optional('content_type'): str,
        })


    async def ensure_search_index(self):
        index = SearchIndexModel(
            definition={
                "fields": [
                    {
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": 768,
                        "similarity": "cosine",
                    }
                ]
            },
            name=self.SEARCH_INDEX_NAME,
            type="vectorSearch",
        )
        metadata_index = SearchIndexModel(
            definition={
                "fields": [
                    {
                        "type": "vector",
                        "path": "metadata_embedding",
                        "numDimensions": 768,
                        "similarity": "cosine",
                    }
                ]
            },
            name=self.METADATA_INDEX_NAME,
            type="vectorSearch",
        )
        await self.collection.create_search_index(index)
        logger.info(f"Created search index '{self.SEARCH_INDEX_NAME}'")
        await self.collection.create_search_index(metadata_index)
        logger.info(f"Created search index '{self.METADATA_INDEX_NAME}'")

    async def get_last_dt(self, owner: str) -> datetime:
        doc = await self.collection.find_one(
            {'owner': owner},
            {'ingested_at': 1},
            sort=[('ingested_at', -1)]
        )
        if doc and doc.get('ingested_at'):
            return doc['ingested_at']
        return datetime(1970, 1, 1)

    async def exists(self, email_id: str) -> bool:
        return await self.collection.find_one({'id': email_id}, {'_id': 1}) is not None

    async def count(self, owner: str, provider_id: str | None = None) -> int:
        query: dict = {'owner': owner}
        if provider_id is not None:
            query['provider_id'] = provider_id
        return await self.collection.count_documents(query)

    async def get_all(self) -> list[dict]:
        cursor = self.collection.find({}, {'_id': 0, 'embedding': 0})
        return await cursor.to_list()

    async def _vector_search(self, index_name: str, path: str, embedding: list[float], limit: int) -> list[dict]:
        pipeline = [
            {
                '$vectorSearch': {
                    'index': index_name,
                    'path': path,
                    'queryVector': embedding,
                    'numCandidates': 150,
                    'limit': limit,
                }
            },
            {
                '$addFields': {
                    'score': {'$meta': 'vectorSearchScore'},
                }
            },
            {
                '$project': {
                    '_id': 0,
                    'embedding': 0,
                    'metadata_embedding': 0,
                }
            },
        ]
        cursor = await self.collection.aggregate(pipeline)
        return await cursor.to_list()

    async def search(self, embedding: list[float], limit: int = 5) -> list[dict]:
        return await self._vector_search(self.SEARCH_INDEX_NAME, 'embedding', embedding, limit)

    async def search_metadata(self, embedding: list[float], limit: int = 5) -> list[dict]:
        return await self._vector_search(self.METADATA_INDEX_NAME, 'metadata_embedding', embedding, limit)

    STOP_WORDS = frozenset({
        'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can',
        'her', 'was', 'one', 'our', 'out', 'has', 'had', 'his', 'how',
        'its', 'may', 'who', 'did', 'get', 'got', 'let', 'say', 'she',
        'too', 'use', 'from', 'about', 'been', 'have', 'this', 'that',
        'with', 'they', 'will', 'what', 'when', 'where', 'which', 'their',
        'them', 'then', 'some', 'than', 'into', 'could', 'would', 'there',
        'these', 'those', 'does', 'your', 'just', 'also', 'like', 'any',
        'emails', 'email', 'mail', 'send', 'sent', 'anyone', 'anything',
        'something', 'someone',
    })

    async def _text_search(self, query: str, limit: int) -> list[dict]:
        words = [w for w in query.lower().split() if len(w) > 2 and w not in self.STOP_WORDS]
        if not words:
            return []
        pattern = '|'.join(re.escape(w) for w in words)
        regex = {'$regex': pattern, '$options': 'i'}
        cursor = self.collection.find(
            {'$or': [{'from': regex}, {'subject': regex}]},
            {'_id': 0, 'embedding': 0, 'metadata_embedding': 0},
        ).limit(limit)
        return await cursor.to_list()

    async def combined_search(self, embedding: list[float], query: str = '', limit: int = 15) -> list[dict]:
        body_results = await self.search(embedding, limit=limit * 2)
        meta_results = await self.search_metadata(embedding, limit=limit * 2)
        text_results = await self._text_search(query, limit=limit * 2) if query else []

        rrf_scores: dict[str, dict] = {}
        k = 60

        for rank, doc in enumerate(body_results):
            doc_id = doc['id']
            rrf_scores[doc_id] = {'doc': doc, 'score': 1 / (k + rank)}

        for rank, doc in enumerate(meta_results):
            doc_id = doc['id']
            if doc_id in rrf_scores:
                rrf_scores[doc_id]['score'] += 1 / (k + rank)
            else:
                rrf_scores[doc_id] = {'doc': doc, 'score': 1 / (k + rank)}

        text_boost = 3
        for rank, doc in enumerate(text_results):
            doc_id = doc['id']
            if doc_id in rrf_scores:
                rrf_scores[doc_id]['score'] += text_boost * (1 / (k + rank))
            else:
                rrf_scores[doc_id] = {'doc': doc, 'score': text_boost * (1 / (k + rank))}

        ranked = sorted(rrf_scores.values(), key=lambda x: x['score'], reverse=True)
        return [entry['doc'] for entry in ranked[:limit]]


# add function to find last embedded email of a given user
