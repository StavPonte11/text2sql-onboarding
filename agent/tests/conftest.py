import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import os
from dotenv import load_dotenv

# Load the project's .env file automatically so users don't have to source it
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(env_path)

if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
    os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-lf-123"
    os.environ["LANGFUSE_SECRET_KEY"] = "sk-lf-123"
    os.environ["LANGFUSE_BASE_URL"] = "http://localhost:3000"

import json
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

# --- Mock LLM ---


class MockStructuredLLM(RunnableLambda):
    def __init__(self, expected_response=None):
        self.expected_response = expected_response

        def _mock_invoke(x):
            if hasattr(self, "override_response"):
                return self.override_response

            # Attempt to return a generic object with a 'route' attribute for RejectionRoute,
            # and generic fields for other schemas if needed.
            class GenericStructured:
                route = "extractor"
                satisfies_question = True
                alignment_score = 1.0
                reason = "Looks good"
                ambiguity_detected = False
                ambiguity_message = ""
                schema_plan = ""
                candidate_options = []

            return GenericStructured()

        super().__init__(_mock_invoke)


from langchain_core.runnables import RunnableLambda


class MockLLM(RunnableLambda):
    def __init__(self):
        super().__init__(lambda x: AIMessage(content="mocked LLM response"))
        self.structured_calls = []

    def with_structured_output(self, schema, method="json_schema"):
        # Returns a new mock structured LLM. We can customize what it returns later.
        return MockStructuredLLM()


@pytest.fixture(autouse=True)
def mock_llm(request):
    if request.node.get_closest_marker("real_llm") or request.node.get_closest_marker(
        "real_e2e"
    ):
        yield None
        return
    mock_instance = MockLLM()
    with (
        patch("agent.llm.get_llm", return_value=mock_instance),
        patch("agent.nodes.schema_explorer.get_llm", return_value=mock_instance, create=True),
        patch("agent.nodes.refiner.get_llm", return_value=mock_instance),
        patch("agent.nodes.query_builder.get_llm", return_value=mock_instance),
        patch("agent.nodes.extractor.get_llm", return_value=mock_instance),
        patch("agent.nodes.finalizer.get_llm", return_value=mock_instance),
        patch("agent.nodes.schema_explorer.llm", mock_instance, create=True),
        patch("agent.nodes.refiner.llm", mock_instance, create=True),
        patch("agent.nodes.query_builder.llm", mock_instance, create=True),
        patch("agent.graph.llm", mock_instance, create=True),
        patch("agent.nodes.finalizer.llm", mock_instance, create=True),
    ):
        yield mock_instance


# --- Mock Redis ---


class MockRedisPipeline:
    def __init__(self):
        self.commands = []

    def delete(self, *keys):
        self.commands.append(("delete", keys))

    def setex(self, name, time, value):
        self.commands.append(("setex", name, time, value))

    async def execute(self):
        # execute should reflect both queued writes and deletes
        return [True] * len(self.commands)


class MockRedisAsync:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        if isinstance(key, str):
            key = key.encode()
        return self.store.get(key)

    async def mget(self, keys):
        res = []
        for key in keys:
            k = key.encode() if isinstance(key, str) else key
            res.append(self.store.get(k))
        return res

    async def setex(self, key, ttl, value):
        if isinstance(key, str):
            key = key.encode()
        if isinstance(value, str):
            value = value.encode()
        self.store[key] = value

    async def delete(self, key):
        if isinstance(key, str):
            key = key.encode()
        self.store.pop(key, None)

    async def scan(self, cursor=0, match=None, count=100):
        # Extremely simplified scan for testing
        keys = []
        if match:
            # simple wildcard match, e.g., "prefix:*"
            prefix = match.replace("*", "").encode()
            for k in self.store.keys():
                if k.startswith(prefix):
                    keys.append(k)
        return (0, keys)

    def pipeline(self):
        return MockRedisPipeline()


@pytest.fixture
def mock_redis():
    mock_instance = MockRedisAsync()
    with patch("redis.asyncio.from_url", return_value=mock_instance):
        yield mock_instance


# --- Mock Trino ---


@pytest.fixture
def mock_trino():
    from core.trino import TrinoExecutionResult

    def _execute_query_sync(*args, **kwargs):
        return TrinoExecutionResult(
            success=True, rows=[[1, "test"]], columns=["id", "name"], error_message=None
        )

    with patch(
        "core.trino.execute_query_sync", side_effect=_execute_query_sync
    ) as mock_func:
        yield mock_func


# --- Mock Esca Client ---


class MockEscaClientObj:
    def __init__(self):
        self.save_data = AsyncMock(return_value={"esca_id": "mock_esca_123"})


class MockEscaContextManager:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.fixture
def mock_esca():
    client = MockEscaClientObj()

    def _get_client(*args, **kwargs):
        return MockEscaContextManager(client)

    with patch("agent.utils.esca.get_esca_client", side_effect=_get_client):
        yield client


# --- Mock Langfuse ---


@pytest.fixture(autouse=True)
def mock_langfuse(request):
    if request.node.get_closest_marker("real_llm") or request.node.get_closest_marker(
        "real_e2e"
    ):
        yield None
        return

    import agent.langfuse_client

    mock_prompt = MagicMock()
    mock_prompt.get_langchain_prompt.return_value = []

    def _mock_compile(locations_dict=""):
        try:
            d = json.loads(locations_dict)
            return "\n".join([f"{v.strip('@')} = {k}" for k, v in d.items()])
        except Exception:
            return f"Locations available: {locations_dict}"

    mock_prompt.compile.side_effect = _mock_compile

    with (
        patch.object(
            agent.langfuse_client.langfuse_client,
            "get_current_trace_id",
            return_value="mock_trace_id",
            create=True,
        ),
        patch.object(
            agent.langfuse_client.langfuse_client,
            "get_current_observation_id",
            return_value="mock_obs_id",
            create=True,
        ),
        patch.object(
            agent.langfuse_client.langfuse_client, "trace", MagicMock(), create=True
        ),
        patch.object(
            agent.langfuse_client.langfuse_client, "span", MagicMock(), create=True
        ),
        patch.object(
            agent.langfuse_client.langfuse_client,
            "get_prompt",
            return_value=mock_prompt,
            create=True,
        ),
    ):
        yield agent.langfuse_client.langfuse_client
