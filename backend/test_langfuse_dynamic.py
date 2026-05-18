from app.services.langfuse_client import langfuse_client

def main():
    try:
        datasets = langfuse_client.client.client.datasets.list().data
        for d in datasets:
            if d.name.startswith("candidate_"):
                runs = langfuse_client.client.get_dataset_runs(d.name)
                print(f"Dataset {d.name} has runs: {len(runs.data)}")
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    main()
