from db.base import Base
from voluptuous import Schema, Required, Any


class Authorization(Base):
    def __init__(self):
        self.collection_name = 'authorizations'
        self.collection = self.client[self.db_name][self.collection_name]
        self.identity_fields = ['state']

        self.schema = Schema({
            Required('state'): str,
            Required('nonce'): str,
            Required('return_url'): Any(str, None)
        })

    async def ensure_indexes(self):
        await self.collection.create_index('state', unique=True)

    async def get(self, state: str) -> dict | None:
        return await self.collection.find_one({'state': state}, {'_id': 0})

    async def delete(self, state: str) -> None:
        await self.collection.delete_one({'state': state})

