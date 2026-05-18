from sqlmodel import Session, select
from app.db.engine import engine
from app.models.models import Table, TableStatus, GoldenQuestion
from app.services.langfuse_client import langfuse_client
from app.routers.evaluation import PRODUCTION_DATASET_NAME, _build_questions_payload

def fix():
    with Session(engine) as session:
        tables = session.exec(select(Table)).all()
        all_qs = []
        for t in tables:
            if t.name in ["rokets", "tanks"]:
                t.status = TableStatus.production
                session.add(t)
                qs = session.exec(select(GoldenQuestion).where(GoldenQuestion.table_id == t.id)).all()
                all_qs.extend(_build_questions_payload(qs, t))
        session.commit()
        
        if all_qs:
            print(f"Syncing {len(all_qs)} questions to {PRODUCTION_DATASET_NAME}...")
            langfuse_client.ensure_dataset_synced(PRODUCTION_DATASET_NAME, all_qs)
            print("Done!")

if __name__ == "__main__":
    fix()
