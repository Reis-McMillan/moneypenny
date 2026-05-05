from datetime import datetime
from pymongo import MongoClient
from voluptuous import Any, Email as EmailValidator, Schema, Required
import logging

from db.base import Base


logger = logging.getLogger(__name__)


class BaseAuthCache(Base):
    def __init__(self):
        self.collection_name = 'auth_cache'
        self.collection = self.client[self.db_name][self.collection_name]
        self.identity_fields = ['user_id']

        self.schema = Schema({
            Required('user_id'): int,
            Required('email'): EmailValidator(),
            Required('roles'): [str],
            Required('access_token'): str,
            Required('refresh_token'): str,
            Required('expires_at'): datetime,
            Required('mcp_token'): Any(str, None),
            Required('external_tokens'): Any([{
                Required('token_id'): int,
                Required('provider_id'): str,
                Required('subject'): str,
                Required('access_token'): str,
                Required('token_type'): str,
                Required('expires_at'): Any(datetime, None),
                Required('email'): Any(EmailValidator(), None)
            }], None)
        })


class AuthCache(BaseAuthCache):

    async def ensure_indexes(self):
        await self.collection.create_index('user_id', unique=True)
        await self.collection.create_index('expires_at', expireAfterSeconds=0)

    async def get(self, user_id: int) -> dict | None:
        return await self.collection.find_one({'user_id': user_id}, {'_id': 0})


class SyncAuthCache(BaseAuthCache):
    def __init__(self):
        self.client = MongoClient(self.mongo_uri)
        super().__init__()

    def get(self, user_id: int) -> dict | None:
        return self.collection.find_one({'user_id': user_id}, {'_id': 0})

    def upsert(self, obj: dict):
        obj = self.schema(obj)
        query = {f: obj[f] for f in self.identity_fields}
        result = self.collection.update_one(
            query,
            {'$set': obj},
            upsert=True
        )

        return result.did_upsert