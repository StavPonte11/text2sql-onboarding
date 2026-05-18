from langfuse.client import Langfuse
import inspect

lf = Langfuse()
print(inspect.signature(lf.client.dataset_items.create))
