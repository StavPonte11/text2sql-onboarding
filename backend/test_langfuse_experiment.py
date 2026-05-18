from app.services.langfuse_client import langfuse_client
from app.config import settings

def main():
    try:
        res = langfuse_client.client.get_dataset_runs("text2sql_production")
        print("Runs in text2sql_production:", res)
    except Exception as e:
        print("Error getting dataset runs:", e)

    try:
        res2 = langfuse_client.client.get_dataset_runs("text2sql_candidate")
        print("Runs in text2sql_candidate:", res2)
    except Exception as e:
        print("Error getting candidate runs:", e)
        
    try:
        from langfuse import Langfuse
        client = Langfuse()
        print("Experiment runs:")
        # there is no list_dataset_runs in the base client, maybe we can fetch them via dataset
        dataset = client.get_dataset("text2sql_production")
        print(f"Dataset items: {len(dataset.items)}")
        print(f"Dataset runs: {dataset.runs}")
    except Exception as e:
        print("Error dataset:", e)

if __name__ == "__main__":
    main()
