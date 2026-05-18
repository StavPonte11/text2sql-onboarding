import requests
import os
from app.config import settings

def main():
    pub = settings.LANGFUSE_PUBLIC_KEY
    sec = settings.LANGFUSE_SECRET_KEY
    host = settings.LANGFUSE_HOST

    # Try to delete dataset text2sql_production via HTTP
    res = requests.delete(
        f"{host}/api/public/datasets/text2sql_production",
        auth=(pub, sec)
    )
    print(res.status_code, res.text)

if __name__ == "__main__":
    main()
