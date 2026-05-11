from voluptuous import Schema, Required, Email
import logging

from db.base import Base

logger = logging.getLogger(__name__)


class TestUser(Base):
    def __init__(self):
        self.collection_name = 'test_user'
        self.collection = self.client[self.db_name][self.collection_name]
        self.identity_fields = ['first_name', 'last_name']

        self.schema = Schema({
            Required('first_name'): str,
            Required('last_name'): str,
            Required('emails'): [Email()] 
        })

    async def ensure_indexes(self):
        await self.collection.create_index(
            [('first_name', 1), ('last_name', 1)], unique=True
        )

    async def all(self):
        return await self.collection.find({}, {'_id': 0}).to_list()