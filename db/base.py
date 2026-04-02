from pymongo.asynchronous.mongo_client import AsyncMongoClient

import config

class Base:
    def __init__(self):
        self.mongo_uri = config.MONGO_URI
        self.db_name = config.DB_NAME
        self.client = AsyncMongoClient(self.mongo_uri)

    async def upsert(self, obj: dict):
        obj = self.schema(obj)
        query = {f: obj[f] for f in self.identity_fields}
        result = await self.collection.update_one(
            query,
            {'$set': obj},
            upsert=True
        )
        return result.did_upsert
