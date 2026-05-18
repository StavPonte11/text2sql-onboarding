from sqlmodel import Session, select
from app.db.engine import engine
from app.models.models import Table, TableStatus
from app.routers.tables import update_table_status

def main():
    with Session(engine) as session:
        table = session.exec(select(Table)).first()
        if not table:
            print("No table")
            return
            
        print(f"Testing lifecycle for {table.name}")
        print("Demoting to sandbox...")
        update_table_status(table.id, TableStatus.sandbox, session)
        
        print("Approving to production...")
        update_table_status(table.id, TableStatus.production, session)
        
        print("Demoting to degraded...")
        update_table_status(table.id, TableStatus.degraded, session)

if __name__ == "__main__":
    main()
