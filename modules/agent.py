import asyncio
import logging

from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langchain_mcp_adapters.client import MultiServerMCPClient
from ollama import AsyncClient

import config
from db.email import Email

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

    EMBEDDING_MODEL = "nomic-embed-text"

    def __init__(self, ollama_client, mcp_client, email_db, tools):

        self.ollama_client = ollama_client
        self.mcp_client = mcp_client
        self.email_db = email_db

        llm = ChatOllama(
            model="llama3.1",
            base_url=config.OLLAMA_HOST
        )

        tools.append(self._make_search_tool())
        checkpointer = InMemorySaver()

        self.agent = create_agent(
            model=llm,
            system_prompt=self.SYSTEM_PROMPT,
            tools=tools,
            checkpointer=checkpointer,
        )

    def _make_search_tool(self):
        ollama_client = self.ollama_client
        email_db = self.email_db
        embedding_model = self.EMBEDDING_MODEL

        @tool
        async def search_emails(query: str) -> str:
            """Search the user's emails by semantic similarity. Use this to find emails about a topic, from a sender, or matching a description."""
            logger.info(f"search_emails called with query: {query}")
            prefixed_query = f"search_query: {query}"
            response = await ollama_client.embed(model=embedding_model, input=prefixed_query)
            embedding = response.embeddings[0]
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
    async def build(cls):
        email_db = Email()
        await email_db.ensure_search_index()

        ollama_client = AsyncClient(host=config.OLLAMA_HOST)

        mcp_client = MultiServerMCPClient({
            'email': {
                'transport': 'http',
                'url': config.MCP_URL + '/mcp/'
            }
        })

        tools = await mcp_client.get_tools()

        return cls(ollama_client, mcp_client, email_db, tools)


async def spinner(stop_event: asyncio.Event):
    import sys
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    i = 0
    while not stop_event.is_set():
        sys.stdout.write(f"\r{frames[i % len(frames)]} Thinking...")
        sys.stdout.flush()
        i += 1
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=0.1)
        except asyncio.TimeoutError:
            pass
    sys.stdout.write("\r" + " " * 20 + "\r")
    sys.stdout.flush()


async def main():
    agent_wrapper = await Agent.build()
    agent_config = {"configurable": {"thread_id": "1"}}

    while True:
        user_input = await asyncio.to_thread(input, "\nYou: ")
        if user_input.lower() in ("quit", "exit"):
            break

        stop = asyncio.Event()
        spin_task = asyncio.create_task(spinner(stop))

        response = await agent_wrapper.agent.ainvoke(
            {"messages": [{"role": "user", "content": user_input}]},
            config=agent_config,
        )

        stop.set()
        await spin_task

        print(f"\nAssistant: {response['messages'][-1].content}")


if __name__ == '__main__':
    asyncio.run(main())
