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

import logging
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import langfuse as sdk
import requests
from langfuse.api import CreateDatasetRunItemRequest

from app.config import settings

# ─── Shared types ──────────────────────────────────────────────────────────────


@dataclass
class Evaluation:
    """Result of an evaluation function."""

    value: float
    comment: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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
        public_key: str | None = None,
        private_key: str | None = None,
        host: str | None = None,
    ) -> None:
        super().__init__()
        self.public_key = public_key or settings.LANGFUSE_PUBLIC_KEY
        self.private_key = private_key or settings.LANGFUSE_SECRET_KEY
        self.host = host or settings.LANGFUSE_HOST
        self.client: sdk.Langfuse | None = None

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
            self.logger.warning(
                "[LangfuseTracer] Credentials not configured — tracing disabled."
            )
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

                self.logger.info("[LangfuseTracer] Flushed and logged out.")
            except Exception as exc:
                self.logger.warning(f"[LangfuseTracer] Logout warning: {exc}")
            finally:
                self.client = None

    # ── Prompt helpers (match main app's LangfuseTracer) ──────────────────────

    def get_prompt(self, name: str) -> Any | None:
        """
        Fetch a prompt from Langfuse by name.

        Returns the prompt object or None if unavailable.
        """
        if self.client is None:
            self.logger.warning(
                "[LangfuseTracer] get_prompt called but client is not connected."
            )
            return None
        try:
            return self.client.get_prompt(name)
        except Exception as exc:
            self.logger.error(f"[LangfuseTracer] get_prompt('{name}') failed: {exc}")
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

    def sync_dataset(self, dataset_name: str, questions: list) -> object:
        """
        Perform a true bidirectional sync of ``dataset_name`` with ``questions``.

        The dataset will ALWAYS exactly reflect the provided question list after
        this call returns:

        * New questions   → added as new dataset items.
        * Changed items   → deleted and re-created (Langfuse has no update API).
        * Stale items     → deleted (they no longer belong to any production table).

        Uses ``metadata.question_id`` as the stable identity key.

        Args:
            dataset_name: Target Langfuse dataset name (created if absent).
            questions:    Desired list of question dicts with keys:
                              question_id, question_text, expected_sql,
                              table_id, schema_name, split, difficulty,
                              question_type.

        Returns:
            The Langfuse dataset object, or None if Langfuse is disabled.
        """
        if not self.enabled:
            self.logger.info(
                f"[LangfuseDatasetService] Langfuse disabled — skipping sync of '{dataset_name}'"
            )
            return None

        # Ensure the dataset exists (no-op if it already does).
        try:
            self._tracer.client.create_dataset(name=dataset_name)
        except Exception as exc:
            self.logger.warning(
                f"[LangfuseDatasetService] create_dataset warning: {exc}"
            )

        # ── Fetch current state from Langfuse ──────────────────────────────────
        pub = self._tracer.public_key
        sec = self._tracer.private_key
        host = self._tracer.host

        existing_items: list[dict] = []  # raw Langfuse item dicts
        try:
            page = 1
            while True:
                res = requests.get(
                    f"{host}/api/public/dataset-items"
                    f"?datasetName={dataset_name}&limit=100&page={page}",
                    auth=(pub, sec),
                )
                if res.status_code != 200:
                    self.logger.warning(
                        f"[LangfuseDatasetService] Could not fetch existing items "
                        f"(status {res.status_code}) — aborting sync."
                    )
                    return None
                batch = res.json().get("data", [])
                existing_items.extend(batch)
                # Langfuse paginates; stop when a partial page is returned.
                if len(batch) < 100:
                    break
                page += 1
        except Exception as exc:
            self.logger.error(
                f"[LangfuseDatasetService] Error fetching dataset items: {exc}"
            )
            return None

        # Build lookup: question_id → {langfuse_item_id, question_text, expected_sql}
        existing_by_qid: dict[str, dict] = {}
        for item in existing_items:
            qid = (item.get("metadata") or {}).get("question_id")
            if qid:
                existing_by_qid[qid] = {
                    "langfuse_id": item["id"],
                    "question_text": (item.get("input") or {}).get("query", ""),
                    "expected_sql": (item.get("expectedOutput") or {}).get(
                        "response", ""
                    ),
                }

        # Build lookup for desired state: question_id → question dict
        desired_by_qid: dict[str, dict] = {q["question_id"]: q for q in questions}

        desired_qids = set(desired_by_qid.keys())
        existing_qids = set(existing_by_qid.keys())

        to_add = desired_qids - existing_qids  # new questions
        to_remove = existing_qids - desired_qids  # stale questions
        to_check = desired_qids & existing_qids  # may need update

        # Identify changed questions (text or SQL differs)
        to_update: set[str] = set()
        for qid in to_check:
            desired = desired_by_qid[qid]
            existing = existing_by_qid[qid]
            if (
                desired["question_text"] != existing["question_text"]
                or desired["expected_sql"] != existing["expected_sql"]
            ):
                to_update.add(qid)

        self.logger.info(
            f"[LangfuseDatasetService] Syncing '{dataset_name}': "
            f"+{len(to_add)} new, ~{len(to_update)} updated, "
            f"-{len(to_remove)} stale  (total desired={len(desired_qids)})"
        )

        # ── Delete stale + changed items ───────────────────────────────────────
        items_to_delete = to_remove | to_update
        for qid in items_to_delete:
            lf_id = existing_by_qid[qid]["langfuse_id"]
            try:
                del_res = requests.delete(
                    f"{host}/api/public/dataset-items/{lf_id}",
                    auth=(pub, sec),
                )
                if del_res.status_code not in (200, 204):
                    self.logger.error(
                        f"[LangfuseDatasetService] Failed to delete item {lf_id} "
                        f"(qid={qid}): {del_res.status_code} {del_res.text}"
                    )
                else:
                    action = "stale" if qid in to_remove else "changed"
                    self.logger.debug(
                        f"[LangfuseDatasetService] Deleted {action} item {lf_id} (qid={qid})"
                    )
            except Exception as exc:
                self.logger.error(
                    f"[LangfuseDatasetService] Error deleting item {lf_id}: {exc}"
                )

        # ── Create new + re-create updated items ───────────────────────────────
        items_to_create = to_add | to_update
        for qid in items_to_create:
            q = desired_by_qid[qid]
            try:
                self._tracer.client.create_dataset_item(
                    dataset_name=dataset_name,
                    id=q["question_id"],
                    input={
                        "query": q["question_text"],
                        "databases": [q.get("schema_name", q["table_id"])],
                    },
                    expected_output={"response": q["expected_sql"]},
                    metadata={
                        "split": q.get("split", ""),
                        "difficulty": str(q.get("difficulty", "")).lower().strip(),
                        "question_id": q["question_id"],
                        "question_type": str(q.get("question_type", ""))
                        .lower()
                        .strip(),
                        "table_id": q.get("table_id", ""),
                    },
                )
            except Exception as exc:
                self.logger.error(
                    f"[LangfuseDatasetService] Failed to create item for qid={qid}: {exc}"
                )

        self.flush()

        self.logger.info(
            f"[LangfuseDatasetService] Sync complete for '{dataset_name}': "
            f"{len(desired_qids)} items now in dataset."
        )

        try:
            return self._tracer.client.get_dataset(dataset_name)
        except Exception as exc:
            self.logger.error(
                f"[LangfuseDatasetService] Could not retrieve dataset after sync: {exc}"
            )
            return None

    # Keep ensure_dataset_synced as a thin alias so any callers that were not
    # yet updated continue to work.  New code should call sync_dataset instead.
    def ensure_dataset_synced(self, dataset_name: str, questions: list) -> object:
        """Deprecated alias for sync_dataset. Use sync_dataset for new code."""
        return self.sync_dataset(dataset_name, questions)

    def clear_dataset(self, dataset_name: str) -> None:
        """
        Deletes all items in a dataset.
        """
        if not self.enabled:
            return

        try:
            pub = self._tracer.public_key
            sec = self._tracer.private_key
            host = self._tracer.host

            res = requests.get(
                f"{host}/api/public/dataset-items?datasetName={dataset_name}",
                auth=(pub, sec),
            )
            if res.status_code != 200:
                self.logger.warning(
                    f"[LangfuseDatasetService] Failed to fetch items for clear: {res.status_code} {res.text}"
                )
                return

            data = res.json().get("data", [])
            for item in data:
                requests.delete(
                    f"{host}/api/public/dataset-items/{item['id']}", auth=(pub, sec)
                )
            self.logger.info(
                f"[LangfuseDatasetService] Cleared {len(data)} items from dataset '{dataset_name}'."
            )
        except Exception as exc:
            self.logger.error(
                f"[LangfuseDatasetService] Error clearing dataset '{dataset_name}': {exc}"
            )

    def remove_table_questions_from_dataset(
        self, dataset_name: str, table_id: str
    ) -> None:
        """
        Removes all dataset items belonging to a specific table from a dataset.
        Since Langfuse SDK does not expose delete natively, this uses requests.
        """
        if not self.enabled:
            return

        try:
            # 1. Fetch all items in the dataset
            pub = self._tracer.public_key
            sec = self._tracer.private_key
            host = self._tracer.host

            res = requests.get(
                f"{host}/api/public/dataset-items?datasetName={dataset_name}",
                auth=(pub, sec),
            )
            if res.status_code != 200:
                self.logger.warning(
                    f"[LangfuseDatasetService] Failed to fetch items: {res.status_code} {res.text}"
                )
                return

            data = res.json().get("data", [])

            # 2. Filter items belonging to the table
            items_to_delete = [
                item
                for item in data
                if item.get("metadata", {}).get("table_id") == table_id
            ]

            if not items_to_delete:
                self.logger.info(
                    f"[LangfuseDatasetService] No items found for table {table_id} in {dataset_name}."
                )
                return

            # 3. Delete them
            for item in items_to_delete:
                del_res = requests.delete(
                    f"{host}/api/public/dataset-items/{item['id']}", auth=(pub, sec)
                )
                if del_res.status_code != 200:
                    self.logger.error(
                        f"[LangfuseDatasetService] Failed to delete item {item['id']}: {item['id']} {del_res.text}"
                    )
                else:
                    self.logger.debug(
                        f"[LangfuseDatasetService] Deleted dataset item {item['id']} for table {table_id}."
                    )

            self.logger.info(
                f"[LangfuseDatasetService] Removed {len(items_to_delete)} questions for table {table_id} from {dataset_name}."
            )
        except Exception as exc:
            self.logger.error(
                f"[LangfuseDatasetService] Error removing questions: {exc}"
            )

    def append_questions_to_dataset(self, dataset_name: str, questions: list) -> bool:
        """
        Deprecated — kept for backward compatibility.

        New callers should use sync_dataset() which performs a true bidirectional
        sync (adds new items, removes stale ones, updates changed items).

        This wrapper delegates to sync_dataset and always returns True unless
        Langfuse is disabled.
        """
        self.logger.warning(
            "[LangfuseDatasetService] append_questions_to_dataset is deprecated; "
            "use sync_dataset instead."
        )
        result = self.sync_dataset(dataset_name, questions)
        return result is not None

    def wait_for_run_items(
        self,
        dataset_name: str,
        run_name: str,
        expected_count: int,
        *,
        max_attempts: int | None = None,
        initial_delay: float | None = None,
        backoff_factor: float | None = None,
    ) -> bool:
        """
        Poll the Langfuse API until the dataset run has ``expected_count`` run
        items persisted, or until the attempt budget is exhausted.

        This is the synchronisation point that gates dataset-item cleanup after
        an evaluation run.  Deleting items before this returns True causes
        Langfuse to record 0 run items for the evaluation (race condition on
        slow / private-network deployments).

        The polling uses exponential back-off so it reacts as soon as
        Langfuse is ready rather than waiting a fixed duration.  All tuning
        knobs have environment-variable overrides:

            LANGFUSE_WAIT_MAX_ATTEMPTS        (default 20)
            LANGFUSE_WAIT_INITIAL_DELAY_SECS  (default 0.5)
            LANGFUSE_WAIT_BACKOFF_FACTOR      (default 1.5)

        Args:
            dataset_name:   Langfuse dataset name to inspect.
            run_name:       Evaluation run name to count items for.
            expected_count: Number of run items that must be present.
            max_attempts:   Override for LANGFUSE_WAIT_MAX_ATTEMPTS.
            initial_delay:  Override for LANGFUSE_WAIT_INITIAL_DELAY_SECS.
            backoff_factor: Override for LANGFUSE_WAIT_BACKOFF_FACTOR.

        Returns:
            True  — ``expected_count`` items confirmed before the budget ran out.
            False — timed out; caller should log a warning and proceed anyway.
        """
        if not self.enabled:
            return True  # Nothing to wait for when Langfuse is disabled.

        if expected_count <= 0:
            return True  # Vacuously satisfied.

        _max_attempts = (
            max_attempts
            if max_attempts is not None
            else settings.LANGFUSE_WAIT_MAX_ATTEMPTS
        )
        _initial_delay = (
            initial_delay
            if initial_delay is not None
            else settings.LANGFUSE_WAIT_INITIAL_DELAY_SECS
        )
        _backoff_factor = (
            backoff_factor
            if backoff_factor is not None
            else settings.LANGFUSE_WAIT_BACKOFF_FACTOR
        )

        delay = _initial_delay
        for attempt in range(1, _max_attempts + 1):
            try:
                # Use the SDK's datasets.get_run() — hits the correct v3 endpoint:
                #   GET /api/public/datasets/{dataset_name}/runs/{run_name}
                # Returns DatasetRunWithItems whose .dataset_run_items is the
                # authoritative list of persisted run items.
                #
                # The legacy /api/public/dataset-run-items?runName=... endpoint
                # returns 400 "expected string got undefined" on Langfuse v3
                # because the query-parameter name changed.  Using the SDK
                # avoids that problem entirely.
                run_with_items = self._tracer.client.client.datasets.get_run(
                    dataset_name, run_name
                )
                total = len(run_with_items.dataset_run_items)
                self.logger.debug(
                    f"[LangfuseDatasetService] wait_for_run_items: "
                    f"{total}/{expected_count} items persisted "
                    f"(attempt {attempt}/{_max_attempts})"
                )
                if total >= expected_count:
                    self.logger.info(
                        f"[LangfuseDatasetService] wait_for_run_items: "
                        f"confirmed {total}/{expected_count} items after {attempt} attempt(s) "
                        f"for run '{run_name}' on dataset '{dataset_name}'."
                    )
                    return True
            except Exception as exc:
                self.logger.warning(
                    f"[LangfuseDatasetService] wait_for_run_items: "
                    f"request error on attempt {attempt}: {exc}"
                )

            if attempt < _max_attempts:
                time.sleep(delay)
                delay = round(delay * _backoff_factor, 3)

        self.logger.warning(
            f"[LangfuseDatasetService] wait_for_run_items: timed out after "
            f"{_max_attempts} attempts waiting for {expected_count} run items "
            f"(run='{run_name}', dataset='{dataset_name}'). "
            f"Proceeding — dataset cleanup may race with Langfuse ingestion."
        )
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
        task: Callable[[Any], dict[str, Any]],
        run_name: str | None = None,
        run_description: str | None = None,
        run_metadata: dict | None = None,
        evaluators: list[Callable[[Any, dict[str, Any]], Evaluation]] | None = None,
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
                                        observation_id=task_result.get(
                                            "observation_id"
                                        ),
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
