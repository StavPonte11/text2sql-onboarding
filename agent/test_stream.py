from deep_agent.agent import create_deep_agent
from langchain_core.messages import HumanMessage
import asyncio

async def test():
    agent = create_deep_agent()
    config = {"configurable": {"thread_id": "cli_deep_agent"}}
    async for chunk, metadata in agent.astream({"messages": [HumanMessage(content="what cities are in Haifa")]}, config, stream_mode="messages"):
        if chunk.type == "ai":
            print(f"AI Chunk: content={repr(chunk.content)}, tool_call_chunks={getattr(chunk, 'tool_call_chunks', None)}")

asyncio.run(test())
