from app.services.langfuse_client import langfuse_client
from langfuse.client import Langfuse

def main():
    lf = Langfuse()
    print("Methods on lf.datasets:")
    print(dir(lf.client.datasets))
    
    # Try deleting a dataset item using api client
    print("\nMethods on api client dataset_items:")
    print(dir(lf.client.client.dataset_items))
    
if __name__ == "__main__":
    main()
