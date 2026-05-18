import requests
import os
from app.config import settings

def main():
    pub = settings.LANGFUSE_PUBLIC_KEY
    sec = settings.LANGFUSE_SECRET_KEY
    host = settings.LANGFUSE_HOST

    # fetch dataset items
    res = requests.get(
        f"{host}/api/public/datasets/text2sql_production",
        auth=(pub, sec)
    )
    print("GET dataset:", res.status_code)
    
    # Let's try to fetch dataset items
    res2 = requests.get(
        f"{host}/api/public/dataset-items?datasetName=text2sql_production",
        auth=(pub, sec)
    )
    print("GET items:", res2.status_code)
    data = res2.json()
    if data.get("data"):
        item_id = data["data"][0]["id"]
        # Try DELETE
        del_res = requests.delete(
            f"{host}/api/public/dataset-items/{item_id}",
            auth=(pub, sec)
        )
        print("DELETE item:", del_res.status_code, del_res.text)

if __name__ == "__main__":
    main()
