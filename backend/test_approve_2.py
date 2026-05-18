from sqlmodel import Session, select
from app.db.engine import engine
from app.models.models import Table, TableStatus
from app.routers.admin_approval import _sync_questions_to_production_dataset

def main():
    with Session(engine) as session:
        table = session.exec(select(Table).where(Table.name == "rokets")).first()
        if table:
            _sync_questions_to_production_dataset(table, session)
            print("Done")

if __name__ == "__main__":
    main()
