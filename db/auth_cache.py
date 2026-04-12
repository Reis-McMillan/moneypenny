from datetime import datetime
from base import Base
from voluptuous import Email, Schema, Required


class AuthCache(Base):
    def __init__(self):
        self.collection_name = 'auth_cache'
        self.collection = self.client[self.db_name][self.collection_name]
        self.identity_fields = ['user_id']

        self.schema = Schema({
            Required('user_id'): int,
            Required('email'): Email(),
            Required('roles'): list[str],
            Required('access_token'): str,
            Required('refresh_token'): str,
            Required('mcp_token'): str | None,
            Required('external_tokens'): list[dict] | None,
            Required('expires_at'): datetime
        })

    async def ensure_indexes(self):
        await self.collection.create_index('user_id', unique=True)
        await self.collection.create_index('expires_at', expireAfterSeconds=0)

    async def get(self, user_id: int) -> dict | None:
        return await self.collection.find_one({'user_id': user_id}, {'_id': 0})