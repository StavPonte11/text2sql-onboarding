from app.services.langfuse_client import langfuse_client
from langfuse.client import Langfuse

def main():
    lf = Langfuse()
    print("Methods on lf.dataset_items:")
    print(dir(lf.client.dataset_items))
    print("\nMethods on lf.datasets:")
    print(dir(lf.client.datasets))

if __name__ == "__main__":
    main()
