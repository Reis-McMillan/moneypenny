import asyncio
from contextlib import asynccontextmanager
import logging
from starlette.applications import Starlette
import uvicorn

import config
from db.email import Email
from db.auth_cache import AuthCache
from db.authorization import Authorization
from modules.ingest import run_ingest
from modules.agent import Agent, spinner

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _run_ingest_process():
    asyncio.run(run_ingest())


async def chat():
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


async def main():
    @asynccontextmanager
    async def lifespan(app):
        app.state.sessions = {}
        app.state.db.email = Email()
        app.state.db.auth_cache = AuthCache()
        app.state.db.authorization = Authorization()
        await app.state.db.authorization.ensure_indexes()
        await app.state.db.auth_cache.ensure_indexes()

        yield

    app = Starlette(lifespan=lifespan)

    logger.info(f"Starting HTTP server on {config.HOST}:{config.PORT}")
    uv_config = uvicorn.Config(app, host=config.HOST, port=config.PORT, log_level="info")
    uv_server = uvicorn.Server(uv_config)
    await uv_server.serve()

if __name__ == '__main__':
    asyncio.run(main())
