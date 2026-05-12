"""
langfuse_client.py — TextToSQL Langfuse Client wrapper with Experiment support.
"""
import os
import os
import langfuse as sdk
from langfuse.client import DatasetClient
from langfuse.api.resources.dataset_run_items.types.create_dataset_run_item_request import CreateDatasetRunItemRequest
from langfuse.decorators import langfuse_context
from app.config import settings
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable

@dataclass
class Evaluation:
    """
    Result of an evaluation function.
    """
    value: float
    comment: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

# Export to os.environ so the decorator client picks them up
if settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
    os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
    os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_HOST

class Langfuse:
    """
    Wrapper for the Langfuse SDK client.
    """
    def __init__(self):
        self.enabled = bool(settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY)
        self.client = None
        if self.enabled:
            try:
                self.client = sdk.Langfuse(
                    public_key=settings.LANGFUSE_PUBLIC_KEY,
                    secret_key=settings.LANGFUSE_SECRET_KEY,
                    host=settings.LANGFUSE_HOST,
                )
            except Exception as e:
                self.enabled = False
                print(f"[Langfuse] Failed to initialize client: {e}")

    def get_dataset(self, name: str):
        if not self.enabled or not self.client:
            raise RuntimeError("Langfuse is not enabled or client not initialized.")
        return self.client.get_dataset(name)

    def flush(self):
        if self.client:
            self.client.flush()
            langfuse_context.flush()

    def sync_question_to_dataset(self, **kwargs) -> bool:
        if not self.enabled or not self.client:
            return False
        try:
            dataset_name = f"text2sql_{kwargs['table_id'][:8]}"
            try:
                self.client.create_dataset(name=dataset_name)
            except:
                pass
            self.client.create_dataset_item(
                dataset_name=dataset_name,
                input={"question": kwargs["question_text"]},
                expected_output={"expected_sql": kwargs["expected_sql"]},
                metadata={
                    "question_id": kwargs["question_id"],
                    "question_type": str(kwargs["question_type"]).lower().strip(),
                    "difficulty": str(kwargs["difficulty"]).lower().strip(),
                },
            )
            self.flush()
            return True
        except Exception:
            return False

    def link_trace_to_dataset_run(self, **kwargs):
        if not self.enabled or not self.client:
            return
        try:
            # Wrap parameters in CreateDatasetRunItemRequest
            request = CreateDatasetRunItemRequest(
                run_name=kwargs.get("run_name"),
                run_description=kwargs.get("run_description"),
                metadata=kwargs.get("run_metadata"),  # Correct field name is 'metadata'
                dataset_item_id=kwargs.get("dataset_item_id"),
                trace_id=kwargs.get("trace_id"),
                observation_id=kwargs.get("observation_id")
            )
            self.client.client.dataset_run_items.create(request=request)
            print(f"[Langfuse] Linked trace {kwargs.get('trace_id')} to dataset item {kwargs.get('dataset_item_id')}")
        except Exception as e:
            print(f"[Langfuse] Link failed: {e}")

    def run_experiment(
        self,
        dataset_name: str,
        task: Callable[[Any], Dict[str, Any]],
        run_name: Optional[str] = None,
        run_description: Optional[str] = None,
        run_metadata: Optional[dict] = None,
        evaluators: Optional[List[Callable[[Any, Dict[str, Any]], Evaluation]]] = None,
    ):
        if not self.enabled or not self.client:
            return None
        
        try:
            dataset = self.client.get_dataset(dataset_name)
            print(f"[Langfuse] Running experiment on dataset '{dataset_name}' with {len(dataset.items)} items")
            results = []
            for item in dataset.items:
                try:
                    # 1. Execute the task
                    task_result = task(item)
                    
                    # 2. Run evaluators
                    if evaluators and task_result:
                        trace_id = task_result.get("trace_id")
                        if trace_id:
                            for eval_func in evaluators:
                                try:
                                    evaluation = eval_func(item, task_result)
                                    self.client.score(
                                        trace_id=trace_id,
                                        observation_id=task_result.get("observation_id"),
                                        name=eval_func.__name__,
                                        value=evaluation.value,
                                        comment=evaluation.comment,
                                        metadata=evaluation.metadata
                                    )
                                except Exception as e:
                                    print(f"[Langfuse] Evaluator {eval_func.__name__} failed: {e}")
                    
                    results.append(task_result)
                except Exception as e:
                    print(f"[Langfuse] Task failed for item {item.id}: {e}")
                    results.append(None)
            
            self.flush()
            return results
        except Exception as e:
            print(f"[Langfuse] Experiment failed: {e}")
            return None

# Singleton instance
langfuse_client = Langfuse()

# Monkey-patch DatasetClient
def dataset_run_experiment(self, task, run_name=None, run_description=None, run_metadata=None, evaluators=None):
    return langfuse_client.run_experiment(
        dataset_name=self.name,
        task=task,
        run_name=run_name,
        run_description=run_description,
        run_metadata=run_metadata,
        evaluators=evaluators
    )

if not hasattr(DatasetClient, "run_experiment"):
    DatasetClient.run_experiment = dataset_run_experiment
