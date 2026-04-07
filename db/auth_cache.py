from datetime import datetime
from base import Base
from voluptuous import Email, Schema, Required


class AuthCache(Base):
    def __init__(self):
        super().__init__()
        self.collection_name = 'auth_cache'
        self.collection = self.client[self.db_name][self.collection_name]
        self.identity_fields = ['user']

        self.schema = Schema({
            Required('user'): Email(),
            Required('roles'): list[str],
            Required('access_token'): str,
            Required('refresh_token'): str,
            Required('mcp_token'): str | None,
            Required('external_tokens'): dict | None,
            Required('expires_at'): datetime
        })

    async def ensure_indexes(self):
        await self.collection.create_index('user', unique=True)
        await self.collection.create_index('expires_at', expireAfterSeconds=0)

    async def get(self, user: str) -> dict | None:
        return await self.collection.find_one({'user': user}, {'_id': 0})