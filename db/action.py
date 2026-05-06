from pymongo import MongoClient
from voluptuous import Schema, Required, Email
import logging

from db.base import Base


logger = logging.getLogger(__name__)


class BaseAction(Base):
    def __init__(self):
        self.collection_name = 'action'
        self.collection = self.client[self.db_name][self.collection_name]
        self.identity_fields = ['user_id', 'token_id']

        self.schema = Schema({
            Required('user_id'): int,
            Required('token_id'): int,
            Required('token_email'): Email(),
            Required('reauth_url'): str
        })


class Action(BaseAction):

    async def ensure_indexes(self):
        await self.collection.create_index(
            [('user_id', 1), ('token_id', 1)],
            unique=True,
        )

    async def get(self, user_id: int):
        return await self.collection.find({
            'user_id': user_id,
        }, {'_id': 0}).to_list()


class SyncAction(BaseAction):

    def __init__(self):
        self.client = MongoClient(self.mongo_uri, tz_aware=True)
        super().__init__()

    def get(self, user_id: int):
        return self.collection.find({
            'user_id': user_id,
        }, {'_id': 0}).to_list()
    
    def upsert(self, obj: dict):
        obj = self.schema(obj)
        query = {f: obj[f] for f in self.identity_fields}
        result = self.collection.update_one(
            query,
            {'$set': obj},
            upsert=True
        )

        return result.did_upsert