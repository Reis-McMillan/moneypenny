import logging

import jwt
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent, AgentState
from langchain.tools import tool
from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient
from langchain_mcp_adapters.client import MultiServerMCPClient
from openai import AsyncOpenAI
from typing_extensions import NotRequired

from config import config
from db.email import Email
from db.auth_cache import AuthCache
from modules.tokens import mcp_token_exchange

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ChatState(AgentState):
    title: NotRequired[str]


class Agent:

    SYSTEM_PROMPT = """You are an email assistant. You help the user find, understand, and manage their emails.

    You have access to these tools:

    - search_emails: Search previously ingested emails by semantic similarity. Use this when the user asks about emails they've received, wants summaries, or asks about a topic/sender.
    - send-email: Sends an email to a recipient. Do not use if the user only asked to draft an email. Drafts must be approved before sending. Args: recipient_id, subject, message.
    - trash-email: Moves an email to trash. Always confirm with the user before trashing. Args: email_id.
    - get-unread-emails: Retrieve unread emails. No args.
    - read-email: Retrieves the content of a given email. Args: email_id.
    - mark-email-as-read: Marks a given email as read. Args: email_id.
    - open-email: Opens an email in the browser. Args: email_id.

    IMPORTANT: Always use the appropriate tool to answer questions. Never make up email content. If no tool returns relevant results, say so.

    Be concise and helpful. Ignore emails which are not relevant to the question."""

    def __init__(self, embed_client, email_db, tools, thread_id, username, auth_cache):
        self.embed_client = embed_client
        self.email_db = email_db
        self.username = username
        self.auth_cache = auth_cache
        self.config = {"configurable": {"thread_id": thread_id}}

        self.llm = ChatOpenAI(
            model=config.CHAT_MODEL,
            base_url=config.VLLM_CHAT_URL,
            api_key="none",
        )

        tools.append(self._make_search_tool())
        checkpointer = MongoDBSaver(MongoClient(config.MONGO_URI), db_name=config.DB_NAME)

        self.agent = create_agent(
            model=self.llm,
            system_prompt=self.SYSTEM_PROMPT,
            tools=tools,
            checkpointer=checkpointer,
            state_schema=ChatState,
        )

    def _make_search_tool(self):
        embed_client = self.embed_client
        email_db = self.email_db

        @tool
        async def search_emails(query: str) -> str:
            """Search the user's emails by semantic similarity. Use this to find emails about a topic, from a sender, or matching a description."""
            logger.info(f"search_emails called with query: {query}")
            response = await embed_client.embeddings.create(
                model=config.EMBEDDING_MODEL, input=query
            )
            embedding = response.data[0].embedding
            results = await email_db.combined_search(embedding, query=query)

            logger.info(f"search returned {len(results)} results")
            if not results:
                return "No emails found matching your query."

            output = []
            for email in results:
                logger.info(f"  - {email.get('from', '?')}: {email.get('subject', '?')}")
                entry = f"From: {email.get('from', 'Unknown')}\n"
                entry += f"Subject: {email.get('subject', 'No subject')}\n"
                entry += f"Date: {email.get('date', 'Unknown date')}\n"
                body = email.get('body', '')
                if len(body) > 1500:
                    body = body[:1500] + "..."
                entry += f"Body: {body}"
                output.append(entry)

            return "\n\n---\n\n".join(output)

        return search_emails

    @classmethod
    async def build(cls, thread_id, username, auth_cache):
        email_db = Email()
        await email_db.ensure_search_index()

        embed_client = AsyncOpenAI(base_url=config.VLLM_EMBED_URL, api_key="none")

        mcp_token = await cls._ensure_mcp_token(username, auth_cache)

        mcp_client = MultiServerMCPClient({
            'email': {
                'transport': 'http',
                'url': config.MCP_URL + '/mcp/',
                'headers': {'Authorization': f'Bearer {mcp_token}'}
            }
        })

        tools = await mcp_client.get_tools()

        return cls(embed_client, email_db, tools, thread_id, username, auth_cache)

    @staticmethod
    async def _ensure_mcp_token(username, auth_cache):
        auth = await auth_cache.get(username)
        if not auth:
            raise ValueError(f"No cached auth for user {username}")

        mcp_token = auth.get('mcp_token')
        if mcp_token:
            try:
                jwt.decode(mcp_token, options={"verify_signature": False, "verify_exp": True})
                return mcp_token
            except jwt.ExpiredSignatureError:
                logger.info("MCP token expired for %s, exchanging", username)

        auth = await mcp_token_exchange(username, auth_cache)
        return auth['mcp_token']

    async def chat(self, message):
        async for event in self.agent.astream_events(
            {"messages": [{"role": "user", "content": message}]},
            config=self.config,
            version="v2",
        ):
            if event["event"] == "on_chat_model_stream":
                token = event["data"]["chunk"].content
                if token:
                    yield token

    async def generate_title(self, first_message):
        response = await self.llm.ainvoke(
            f"Generate a short 3-5 word title for a conversation that starts with this message. "
            f"Reply with ONLY the title, nothing else.\n\nMessage: {first_message}"
        )
        title = response.content.strip().strip('"')
        self.agent.update_state(self.config, {"title": title})
        return title

    def get_title(self):
        state = self.agent.get_state(self.config)
        return state.values.get("title", "")

    def get_history(self):
        state = self.agent.get_state(self.config)
        return state.values.get("messages", [])
