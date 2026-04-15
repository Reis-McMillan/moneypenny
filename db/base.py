from pymongo.asynchronous.mongo_client import AsyncMongoClient

from config import config

class Base:
    mongo_uri = config.MONGO_URI
    db_name = config.DB_NAME
    client = AsyncMongoClient(mongo_uri)

    async def upsert(self, obj: dict):
        obj = self.schema(obj)
        query = {f: obj[f] for f in self.identity_fields}
        result = await self.collection.update_one(
            query,
            {'$set': obj},
            upsert=True
        )
        return result.did_upsert
