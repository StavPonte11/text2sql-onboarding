"""
langfuse_client.py — LangfuseTracer + dataset/evaluation helpers for TextToSQL.

Architecture:
  Connection          — Abstract base for any external service connection.
  LangfuseTracer      — Concrete Langfuse connection; mirrors the main app's
                        LangfuseTracer (implements Connection, adds get_prompt
                        and get_prompt_as_langchain).
  LangfuseDatasetService — Holds all dataset-sync, experiment-run, and scoring
                        helpers that are specific to this onboarding app and do
                        NOT belong in the generic tracer.

Merge notes:
  The LangfuseTracer class is designed to be a drop-in replacement for the
  LangfuseTracer used in the main application. When merging, keep this class
  as-is and swap the dataset helpers for the real MCP / Trino calls.
"""
from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import langfuse as sdk
from langfuse.api.resources.dataset_run_items.types.create_dataset_run_item_request import (
    CreateDatasetRunItemRequest,
)
from langfuse.decorators import langfuse_context

from app.config import settings


# ─── Shared types ──────────────────────────────────────────────────────────────

@dataclass
class Evaluation:
    """Result of an evaluation function."""
    value: float
    comment: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ─── Abstract connection base ──────────────────────────────────────────────────

class Connection(ABC):
    """
    Abstract base class for external service connections.

    Mirrors the Connection interface used in the main Text2SQL application,
    ensuring a smooth integration when the two apps are connected.

    Data members:
        logger  — standard Python logger scoped to the subclass.

    Abstract methods (must be implemented by subclasses):
        connect()   — establish / re-establish the connection.
        is_alive()  — return True if the connection is healthy.
        logout()    — gracefully close / clean up the connection.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def connect(self) -> None:
        """Establish or re-establish the external connection."""
        ...

    @abstractmethod
    def is_alive(self) -> bool:
        """Return True if the connection is healthy and usable."""
        ...

    @abstractmethod
    def logout(self) -> None:
        """Gracefully close / clean up the connection."""
        ...


# ─── LangfuseTracer ───────────────────────────────────────────────────────────

class LangfuseTracer(Connection):
    """
    Langfuse connection — mirrors LangfuseTracer from the main application.

    Data members (DMs):
        public_key  — Langfuse public API key.
        private_key — Langfuse secret API key.
        host        — Langfuse server URL.
        client      — Instantiated sdk.Langfuse client (None if disabled).

    Implements Connection: connect(), is_alive(), logout().

    Additional methods (present in the main app's LangfuseTracer):
        get_prompt(name)                  — fetch a prompt by name.
        get_prompt_as_langchain(name)     — fetch and convert to a LangChain prompt.
    """

    def __init__(
        self,
        public_key: Optional[str] = None,
        private_key: Optional[str] = None,
        host: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.public_key = public_key or settings.LANGFUSE_PUBLIC_KEY
        self.private_key = private_key or settings.LANGFUSE_SECRET_KEY
        self.host = host or settings.LANGFUSE_HOST
        self.client: Optional[sdk.Langfuse] = None

        # Expose keys to environment so decorator-based tracing also picks them up
        if self.public_key and self.private_key:
            os.environ["LANGFUSE_PUBLIC_KEY"] = self.public_key
            os.environ["LANGFUSE_SECRET_KEY"] = self.private_key
            os.environ["LANGFUSE_HOST"] = self.host

        self.connect()

    # ── Connection interface ───────────────────────────────────────────────────

    def connect(self) -> None:
        """Establish the Langfuse client connection."""
        if not (self.public_key and self.private_key):
            self.logger.warning("[LangfuseTracer] Credentials not configured — tracing disabled.")
            return
        try:
            self.client = sdk.Langfuse(
                public_key=self.public_key,
                secret_key=self.private_key,
                host=self.host,
            )
            self.logger.info("[LangfuseTracer] Connected to Langfuse.")
        except Exception as exc:
            self.client = None
            self.logger.error(f"[LangfuseTracer] Failed to connect: {exc}")

    def is_alive(self) -> bool:
        """Return True if the Langfuse client is initialised and reachable."""
        if self.client is None:
            return False
        try:
            self.client.auth_check()
            return True
        except Exception:
            return False

    def logout(self) -> None:
        """Flush pending events and release the client."""
        if self.client:
            try:
                self.client.flush()
                langfuse_context.flush()
                self.logger.info("[LangfuseTracer] Flushed and logged out.")
            except Exception as exc:
                self.logger.warning(f"[LangfuseTracer] Logout warning: {exc}")
            finally:
                self.client = None

    # ── Prompt helpers (match main app's LangfuseTracer) ──────────────────────

    def get_prompt(self, name: str) -> Optional[Any]:
        """
        Fetch a prompt from Langfuse by name.

        Returns the prompt object or None if unavailable.
        """
        if self.client is None:
            self.logger.warning("[LangfuseTracer] get_prompt called but client is not connected.")
            return None
        try:
            return self.client.get_prompt(name)
        except Exception as exc:
            self.logger.error(f"[LangfuseTracer] get_prompt('{name}') failed: {exc}")
            return None

    def get_prompt_as_langchain(self, name: str) -> Optional[Any]:
        """
        Fetch a Langfuse prompt and return it as a LangChain-compatible prompt.

        Requires langchain to be installed in the environment.
        Returns None if the prompt or langchain is unavailable.
        """
        prompt = self.get_prompt(name)
        if prompt is None:
            return None
        try:
            return prompt.get_langchain_prompt()
        except Exception as exc:
            self.logger.error(f"[LangfuseTracer] get_prompt_as_langchain('{name}') failed: {exc}")
            return None

    # ── Convenience property ───────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """True when the tracer has a live client."""
        return self.client is not None


# ─── Dataset & experiment helpers (onboarding-app specific) ───────────────────

class LangfuseDatasetService:
    """
    Wraps a LangfuseTracer and provides dataset-sync, experiment-run, and
    evaluation-linkage helpers that are specific to this onboarding application.

    These functions are intentionally separated from LangfuseTracer because:
      • They are not part of the generic connection interface.
      • They will be replaced by real MCP/Trino calls when the apps are merged.
      • Keeping them here makes the merge diff minimal and focused.
    """

    def __init__(self, tracer: LangfuseTracer) -> None:
        self._tracer = tracer
        self.logger = logging.getLogger(self.__class__.__name__)

    # ── Pass-through helpers ───────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._tracer.enabled

    @property
    def client(self):
        return self._tracer.client

    def flush(self) -> None:
        if self._tracer.client:
            self._tracer.client.flush()
            langfuse_context.flush()

    # ── Dataset helpers ────────────────────────────────────────────────────────

    def get_dataset(self, name: str):
        if not self.enabled:
            raise RuntimeError("Langfuse is not enabled or client not initialized.")
        return self._tracer.client.get_dataset(name)

    def dataset_exists(self, name: str) -> bool:
        """Check whether a dataset exists in Langfuse."""
        if not self.enabled:
            return False
        try:
            self._tracer.client.get_dataset(name)
            return True
        except Exception:
            return False

    def ensure_dataset_synced(self, dataset_name: str, questions: list) -> object:
        """
        Ensure the Langfuse dataset exists and contains all given questions.
        Creates/recreates the dataset if missing, then upserts every item.
        Returns the dataset object (ready for run_experiment).
        """
        if not self.enabled:
            return None
        try:
            self._tracer.client.create_dataset(name=dataset_name)
        except Exception as exc:
            self.logger.warning(f"[LangfuseDatasetService] create_dataset warning: {exc}")

        self.logger.info(
            f"[LangfuseDatasetService] Syncing {len(questions)} questions to '{dataset_name}'"
        )
        for q in questions:
            try:
                self._tracer.client.create_dataset_item(
                    dataset_name=dataset_name,
                    input={
                        "query": q["question_text"],
                        "databases": [q.get("schema_name", q["table_id"])],
                    },
                    expected_output={"response": q["expected_sql"]},
                    metadata={
                        "split": q.get("split", ""),
                        "difficulty": str(q.get("difficulty", "")).lower().strip(),
                        "question_id": q["question_id"],
                        "question_type": str(q.get("question_type", "")).lower().strip(),
                    },
                )
            except Exception as exc:
                self.logger.error(
                    f"[LangfuseDatasetService] Failed to upsert question "
                    f"{q.get('question_id')}: {exc}"
                )

        self.flush()
        try:
            return self._tracer.client.get_dataset(dataset_name)
        except Exception as exc:
            self.logger.error(
                f"[LangfuseDatasetService] Could not retrieve dataset after sync: {exc}"
            )
            return None

    def sync_question_to_dataset(self, **kwargs) -> bool:
        if not self.enabled:
            return False
        try:
            dataset_name = f"text2sql_{kwargs['table_id'][:8]}"
            try:
                self._tracer.client.create_dataset(name=dataset_name)
            except Exception:
                pass
            self._tracer.client.create_dataset_item(
                dataset_name=dataset_name,
                input={
                    "query": kwargs["question_text"],
                    "databases": [kwargs.get("schema_name", kwargs["table_id"])],
                },
                expected_output={"response": kwargs["expected_sql"]},
                metadata={
                    "split": kwargs.get("split", ""),
                    "difficulty": str(kwargs.get("difficulty", "")).lower().strip(),
                    "question_id": kwargs["question_id"],
                    "question_type": str(kwargs["question_type"]).lower().strip(),
                },
            )
            self.flush()
            return True
        except Exception:
            return False

    def link_trace_to_dataset_run(self, **kwargs) -> None:
        if not self.enabled:
            return
        try:
            request = CreateDatasetRunItemRequest(
                run_name=kwargs.get("run_name"),
                run_description=kwargs.get("run_description"),
                metadata=kwargs.get("run_metadata"),
                dataset_item_id=kwargs.get("dataset_item_id"),
                trace_id=kwargs.get("trace_id"),
                observation_id=kwargs.get("observation_id"),
            )
            self._tracer.client.client.dataset_run_items.create(request=request)
            self.logger.info(
                f"[LangfuseDatasetService] Linked trace {kwargs.get('trace_id')} "
                f"to dataset item {kwargs.get('dataset_item_id')}"
            )
        except Exception as exc:
            self.logger.error(f"[LangfuseDatasetService] Link failed: {exc}")

    def run_experiment(
        self,
        dataset_name: str,
        task: Callable[[Any], Dict[str, Any]],
        run_name: Optional[str] = None,
        run_description: Optional[str] = None,
        run_metadata: Optional[dict] = None,
        evaluators: Optional[List[Callable[[Any, Dict[str, Any]], Evaluation]]] = None,
    ):
        if not self.enabled:
            return None
        try:
            dataset = self._tracer.client.get_dataset(dataset_name)
            self.logger.info(
                f"[LangfuseDatasetService] Running experiment on '{dataset_name}' "
                f"with {len(dataset.items)} items"
            )
            results = []
            for item in dataset.items:
                try:
                    task_result = task(item)
                    if evaluators and task_result:
                        trace_id = task_result.get("trace_id")
                        if trace_id:
                            for eval_func in evaluators:
                                try:
                                    evaluation = eval_func(item, task_result)
                                    self._tracer.client.score(
                                        trace_id=trace_id,
                                        observation_id=task_result.get("observation_id"),
                                        name=eval_func.__name__,
                                        value=evaluation.value,
                                        comment=evaluation.comment,
                                        metadata=evaluation.metadata,
                                    )
                                except Exception as exc:
                                    self.logger.error(
                                        f"[LangfuseDatasetService] Evaluator "
                                        f"{eval_func.__name__} failed: {exc}"
                                    )
                    results.append(task_result)
                except Exception as exc:
                    self.logger.error(
                        f"[LangfuseDatasetService] Task failed for item {item.id}: {exc}"
                    )
                    results.append(None)

            self.flush()
            return results
        except Exception as exc:
            self.logger.error(f"[LangfuseDatasetService] Experiment failed: {exc}")
            return None


# ─── Module-level singletons ──────────────────────────────────────────────────

# The tracer — use this wherever you need generic Langfuse tracing / prompts.
langfuse_tracer = LangfuseTracer()

# The dataset service — use this for evaluation dataset operations.
# Aliased as `langfuse_client` for backward compatibility with existing imports.
langfuse_client = LangfuseDatasetService(langfuse_tracer)


