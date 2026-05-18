from app.services.langfuse_client import langfuse_client
import requests

def main():
    if not langfuse_client.enabled:
        print("Langfuse disabled")
        return

    pub = langfuse_client._tracer.public_key
    sec = langfuse_client._tracer.private_key
    host = langfuse_client._tracer.host

    res = requests.get(
        f"{host}/api/public/dataset-items?datasetName=text2sql_production",
        auth=(pub, sec)
    )
    if res.status_code == 200:
        data = res.json().get("data", [])
        print(f"text2sql_production has {len(data)} items")
    else:
        print("Failed to get dataset items:", res.status_code, res.text)

if __name__ == "__main__":
    main()
