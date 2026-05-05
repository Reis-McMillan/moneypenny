import json
import logging

import httpx
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent, AgentState
from langchain.agents.middleware.human_in_the_loop import HumanInTheLoopMiddleware
from langchain.tools import tool, BaseTool
from langchain_core.tools.structured import StructuredTool
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.types import Command
from pymongo import MongoClient
from langchain_mcp_adapters.client import MultiServerMCPClient
from openai import AsyncOpenAI
from typing_extensions import NotRequired
from config import config
from middleware.authenticated import User
from db.email import Email

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MCPConsentRequired(Exception):
    def __init__(self, redirect_url):
        self.redirect_url = redirect_url
        super().__init__(f"MCP consent required: {redirect_url}")


class ChatState(AgentState):
    title: NotRequired[str]


class Agent:
    # to-do: evaluate this prompt... consider integrations with email-mcp prompts
    BASE_SYSTEM_PROMPT = """Your name is Moneypenny. You are an email assistant. You help the user find, understand, and manage their emails.

    IMPORTANT: Always use the appropriate tool to answer questions. Never make up email content. If no tool returns relevant results, say so.

    Be concise and helpful. Ignore emails which are not relevant to the question."""

    @staticmethod
    def _build_system_prompt(user: User, token_id: int) -> str:
        return Agent.BASE_SYSTEM_PROMPT
    
    @staticmethod
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
    
    @classmethod
    async def build(cls, thread_id, email_db, user: User, token_id: int):
        embed_client = AsyncOpenAI(base_url=config.VLLM_EMBED_URL, api_key="none")
        mcp_token = user.mcp_token

        await cls._check_mcp_heartbeat(mcp_token)
        mcp_client = MultiServerMCPClient({
            'email': {
                'transport': 'http',
                'url': config.MCP_URL + '/mcp/',
                'headers': {'Authorization': f'Bearer {mcp_token}'}
            }
        })

        def _bind_token_id(tool: BaseTool, token_id: int):
            schema = getattr(tool, "args_schema", None)
            if not isinstance(schema, dict):
                return tool
            properties = schema.get("properties", {})
            if "token_id" not in properties:
                return tool

            new_schema = {
                **schema,
                "properties": {k: v for k, v in properties.items() if k != "token_id"},
            }
            if "required" in schema:
                new_schema["required"] = [r for r in schema["required"] if r != "token_id"]

            async def call(**kwargs):
                return await tool.ainvoke({**kwargs, "token_id": token_id})

            return StructuredTool.from_function(
                coroutine=call,
                name=tool.name,
                description=tool.description,
                args_schema=new_schema,
            )

        raw_tools = await mcp_client.get_tools()
        interrupt_on = {
            t.name: {"allowed_decisions": ["approve", "reject"]}
            for t in raw_tools
            if (t.metadata or {}).get("consent-required") is True
        }
        tools = [_bind_token_id(t, token_id) for t in raw_tools]

        return cls(
            embed_client, email_db, mcp_client, tools, thread_id, user,
            token_id=token_id, interrupt_on=interrupt_on,
        )

    def __init__(
            self,
            embed_client: AsyncOpenAI,
            email_db: Email,
            mcp_client: MultiServerMCPClient,
            tools: list[BaseTool],
            thread_id: str,
            user: User,
            token_id: int,
            interrupt_on: dict | None = None,
        ):
        self.embed_client = embed_client
        self.email_db = email_db
        self.mcp_client = mcp_client
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
        middleware = [HumanInTheLoopMiddleware(interrupt_on=interrupt_on)] if interrupt_on else []
        self.agent = create_agent(
            model=self.llm,
            system_prompt=self._build_system_prompt(user, token_id),
            tools=tools,
            checkpointer=checkpointer,
            state_schema=ChatState,
            middleware=middleware,
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

    async def _yield_pending_interrupts(self):
        state = await self.agent.aget_state(self.config)
        interrupts = getattr(state, "interrupts", None) or ()
        for intr in interrupts:
            yield ("interrupt", json.dumps({
                "id": str(intr.id),
                "value": intr.value,
            }, default=str))

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

        async for evt in self._yield_pending_interrupts():
            yield evt

    async def resume(self, decisions: list[dict]):
        async for event in self.agent.astream_events(
            Command(resume={"decisions": decisions}),
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

        async for evt in self._yield_pending_interrupts():
            yield evt

    async def draft_email(
        self,
        content: str,
        recipient: str,
        recipient_email: str,
    ):
        draft_prompt = await self.mcp_client.get_prompt(
            "email",
            "draft-email",
            arguments={
                "content": content,
                "recipient": recipient,
                "recipient_email": recipient_email,
            },
        )

        text = "\n".join(
            m.content if isinstance(m.content, str) else str(m.content)
            for m in draft_prompt
        )
        yield ("user_message", text)

        async for event in self.agent.astream_events(
            {"messages": draft_prompt},
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

        async for evt in self._yield_pending_interrupts():
            yield evt

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
