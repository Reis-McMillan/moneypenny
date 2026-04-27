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
from db.auth_cache import AuthCache
from middleware.authenticated import User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MCPConsentRequired(Exception):
    def __init__(self, redirect_url):
        self.redirect_url = redirect_url
        super().__init__(f"MCP consent required: {redirect_url}")


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

    def __init__(self, embed_client, email_db, tools, thread_id, user: User):
        self.embed_client = embed_client
        self.email_db = email_db
        self.user = user
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
    async def build(cls, thread_id, email_db, user: User):
        embed_client = AsyncOpenAI(base_url=config.VLLM_EMBED_URL, api_key="none")
        mcp_token = user.mcp_token
        
        mcp_client = MultiServerMCPClient({
            'email': {
                'transport': 'http',
                'url': config.MCP_URL + '/mcp/',
                'headers': {'Authorization': f'Bearer {mcp_token}'}
            }
        })

        tools = await mcp_client.get_tools()

        return cls(embed_client, email_db, tools, thread_id, user)

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
