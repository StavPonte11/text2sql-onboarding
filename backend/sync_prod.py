from sqlmodel import Session, select
from app.db.engine import engine
from app.models.models import Table, TableStatus, GoldenQuestion
from app.services.warehouse import add_table_to_warehouse
from app.services.langfuse_client import langfuse_client
from app.routers.evaluation import PRODUCTION_DATASET_NAME, _build_questions_payload

def sync_all():
    with Session(engine) as session:
        prod_tables = session.exec(select(Table).where(Table.status == TableStatus.production)).all()
        print(f"Found {len(prod_tables)} production tables.")
        
        all_questions = []
        for t in prod_tables:
            print(f"Adding '{t.name}' to warehouse...")
            try:
                add_table_to_warehouse(t)
            except Exception as e:
                print(f"Error adding {t.name}: {e}")
                
            qs = session.exec(select(GoldenQuestion).where(GoldenQuestion.table_id == t.id)).all()
            all_questions.extend(_build_questions_payload(qs, t))
            
        print(f"Syncing {len(all_questions)} questions to {PRODUCTION_DATASET_NAME} dataset...")
        langfuse_client.ensure_dataset_synced(PRODUCTION_DATASET_NAME, all_questions)
        print("Done!")

if __name__ == "__main__":
    sync_all()
