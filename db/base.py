from pymongo.asynchronous.mongo_client import AsyncMongoClient
import logging

from config import config

logger = logging.getLogger('db.Base')

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
    
    @classmethod
    async def ensure_collections(cls, collections: list['Base']):
        db = cls.client[cls.db_name]
        existing = await db.list_collection_names()
        for coll in collections:
            if coll.collection_name not in existing:
                await db.create_collection(coll.collection_name)
                logger.info(f"Created collection '{coll.collection_name}'")
