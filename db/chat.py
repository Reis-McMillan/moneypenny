from base import Base
from voluptuous import Email, Schema, Required


class Chat(Base):
    def __init__(self):
        super().__init__()
        self.collection_name = 'chats'
        self.collection = self.client[self.db_name][self.collection_name]
        self.identity_fields = ['id']

        self.schema = Schema({
            Required('title'): str,
            Required('owner'): Email()
        })

    async def ensure_indexes(self):
        await self.collection.create_index(['title', 'owner'], unique=True)

    async def get(self, title, owner) -> dict | None:
        return await self.collection.find_one({
                'title': title,
                'owner': owner
            },
            {'_id': 0}
        )