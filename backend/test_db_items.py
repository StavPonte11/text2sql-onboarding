from sqlmodel import Session, select
from app.db.engine import engine
from app.models.models import Table, TableStatus, GoldenQuestion

def main():
    with Session(engine) as session:
        prod_tables = session.exec(select(Table).where(Table.status == TableStatus.production)).all()
        print(f"Prod tables: {len(prod_tables)}")
        for t in prod_tables:
            qs = session.exec(select(GoldenQuestion).where(GoldenQuestion.table_id == t.id)).all()
            print(f"Table {t.name} has {len(qs)} questions")

if __name__ == "__main__":
    main()
