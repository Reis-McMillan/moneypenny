import json
import logging

import httpx
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent, AgentState
from langchain.tools import tool
from langchain_core.tools.structured import StructuredTool
from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient
from langchain_mcp_adapters.client import MultiServerMCPClient
from openai import AsyncOpenAI
from typing_extensions import NotRequired
from pydantic import create_model

from config import config
from middleware.authenticated import User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MCPConsentRequired(Exception):
    def __init__(self, redirect_url):
        self.redirect_url = redirect_url
        super().__init__(f"MCP consent required: {redirect_url}")


async def _check_mcp_heartbeat(mcp_token: str) -> None:
    async with httpx.AsyncClient() as client:
        probe = await client.get(
            f"{config.MCP_URL.rstrip('/')}/heartbeat",
            headers={'Authorization': f'Bearer {mcp_token}'},
            follow_redirects=False,
        )
    if probe.status_code == 403:
        body = probe.json() if 'application/json' in probe.headers.get('content-type', '') else {}
        if isinstance(body, dict) and body.get('setup_required'):
            raise MCPConsentRequired(body.get('redirect_url'))
    if not probe.is_success:
        raise RuntimeError(
            f"MCP heartbeat failed: {probe.status_code} {probe.text}"
        )


class ChatState(AgentState):
    title: NotRequired[str]


class Agent:

    # to-do: evaluate this prompt... consider integrations with email-mcp prompt
    BASE_SYSTEM_PROMPT = """You are an email assistant. You help the user find, understand, and manage their emails.

    You have access to these tools:

    - search_emails: Search previously ingested emails by semantic similarity. Use this when the user asks about emails they've received, wants summaries, or asks about a topic/sender.
    - send-email: Sends an email to a recipient. Do not use if the user only asked to draft an email. Drafts must be approved before sending. Args: token_id, recipient_id, subject, message.
    - trash-email: Moves an email to trash. Always confirm with the user before trashing. Args: token_id, email_id.
    - get-unread-emails: Retrieve unread emails. Args: token_id (optional — omit to fan out across all accounts).
    - read-email: Retrieves the content of a given email. Args: token_id, email_id.
    - mark-email-as-read: Marks a given email as read. Args: token_id, email_id.
    - open-email: Opens an email in the browser. Args: token_id, email_id.

    Each email tool (other than search_emails) takes a `token_id` (integer) that identifies which connected email account to act on.

    IMPORTANT: Always use the appropriate tool to answer questions. Never make up email content. If no tool returns relevant results, say so.

    Be concise and helpful. Ignore emails which are not relevant to the question."""

    @staticmethod
    def _build_system_prompt(user: User, token_id: int | None) -> str:
        external_tokens = user.external_tokens or []
        if token_id is not None:
            chosen = next((t for t in external_tokens if t.get("token_id") == token_id), None)
            email = (chosen or {}).get("email") or (chosen or {}).get("subject") or "unknown"
            preamble = (
                f"The user has selected the email account with token_id={token_id} ({email}). "
                f"Pass token_id={token_id} to every email tool call unless the user explicitly asks about another account."
            )
        elif len(external_tokens) == 1:
            only = external_tokens[0]
            tid = only.get("token_id")
            email = only.get("email") or only.get("subject") or "unknown"
            preamble = (
                f"The user has exactly one connected email account: token_id={tid} ({email}). "
                f"Pass token_id={tid} to every email tool call."
            )
        else:
            available = ", ".join(
                f"token_id={t.get('token_id')} ({t.get('email') or t.get('subject') or 'unknown'})"
                for t in external_tokens
            ) or "(none)"
            preamble = (
                "The user has not selected a specific account. "
                f"Their connected accounts: {available}. "
                "For read tools (get-unread-emails, search_emails) you may operate across all accounts. "
                "For write/single-target tools (send-email, trash-email, read-email, mark-email-as-read, open-email), "
                "ask the user which account they mean (by listing emails) before calling."
            )
        return preamble + "\n\n" + Agent.BASE_SYSTEM_PROMPT

    def __init__(self, embed_client, email_db, tools, thread_id, user: User, token_id: int | None = None):
        self.embed_client = embed_client
        self.email_db = email_db
        self.user = user
        self.token_id = token_id
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
            system_prompt=self._build_system_prompt(user, token_id),
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
    async def build(cls, thread_id, email_db, user: User, token_id: int):
        embed_client = AsyncOpenAI(base_url=config.VLLM_EMBED_URL, api_key="none")
        mcp_token = user.mcp_token

        await _check_mcp_heartbeat(mcp_token)

        mcp_client = MultiServerMCPClient({
            'email': {
                'transport': 'http',
                'url': config.MCP_URL + '/mcp/',
                'headers': {'Authorization': f'Bearer {mcp_token}'}
            }
        })

        def _bind_token_id(tool: StructuredTool, token_id: int):
            schema = getattr(tool, "args_schema", None)
            if not schema or "token_id" not in schema.model_fields:
                return tool
            
            fields = {
                name: (f.annotation, f) for name, f in schema.model_fields.items() if name != "token_id"
            }
            NewSchema = create_model(f"{schema.__name__}WithoutTokenId", **fields)

            async def call(**kwargs):
                return await tool.ainvoke({**kwargs, "token_id": token_id})
            
            return StructuredTool.from_function(
                coroutine=call,
                name=tool.name,
                description=tool.description,
                args_schema=NewSchema
            )

        raw_tools = await mcp_client.get_tools()
        tools = [_bind_token_id(t, token_id) for t in raw_tools]

        return cls(embed_client, email_db, tools, thread_id, user, token_id=token_id)

    async def chat(self, message):
        async for event in self.agent.astream_events(
            {"messages": [{"role": "user", "content": message}]},
            config=self.config,
            version="v2",
        ):
            if event.get("event") != "on_chat_model_stream":
                continue
            chunk = (event.get("data") or {}).get("chunk")
            if chunk is None:
                continue
            payload = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)
            yield ("chunk", json.dumps(payload, default=str))

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
