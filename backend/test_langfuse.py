import os
import sys
from sqlmodel import Session, select
from app.db.engine import engine
from app.models.models import Table, GoldenQuestion
from app.services.langfuse_client import langfuse_client

def main():
    try:
        ds = langfuse_client.client.get_dataset("text2sql_production")
        print(f"text2sql_production has {len(ds.items)} items")
        if ds.items:
            print("First item metadata:", ds.items[0].metadata)
    except Exception as e:
        print(f"Error fetching production dataset: {e}")

    try:
        datasets = langfuse_client.client.client.datasets.list().data
        for d in datasets:
            print(f"Dataset: {d.name}")
    except Exception as e:
        pass

if __name__ == "__main__":
    main()
