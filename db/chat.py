import logging
from datetime import datetime, timezone
from voluptuous import Schema, Required

from db.base import Base


logger = logging.getLogger(__name__)


class BaseChat(Base):
    def __init__(self):
        self.collection_name = 'chats'
        self.collection = self.client[self.db_name][self.collection_name]
        self.identity_fields = ['thread_id']

        self.schema = Schema({
            Required('thread_id'): str,
            Required('owner'): int,
            Required('title'): str,
            Required('created_at'): datetime,
            Required('updated_at'): datetime,
        })


class Chat(BaseChat):

    async def ensure_indexes(self):
        await self.collection.create_index('thread_id', unique=True)
        await self.collection.create_index([('owner', 1), ('updated_at', -1)])

    async def get(self, thread_id: str) -> dict | None:
        return await self.collection.find_one(
            {'thread_id': thread_id}, {'_id': 0}
        )

    async def list_for_owner(self, owner: int) -> list[dict]:
        cursor = self.collection.find(
            {'owner': owner},
            {'_id': 0, 'thread_id': 1, 'title': 1, 'created_at': 1, 'updated_at': 1},
            sort=[('updated_at', -1)],
        )
        return await cursor.to_list()

    async def touch(self, thread_id: str) -> None:
        await self.collection.update_one(
            {'thread_id': thread_id},
            {'$set': {'updated_at': datetime.now(timezone.utc)}},
        )
