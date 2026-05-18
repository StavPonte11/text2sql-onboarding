import sys
import traceback
from sqlmodel import Session, select
from app.db.engine import engine
from app.models.models import Table, EvalRun, EvalStatus
from app.routers.evaluation import promote_table_to_production_workflow

def main():
    with Session(engine) as session:
        table = session.exec(select(Table)).first()
        if not table:
            print("No table")
            return
        
        run = EvalRun(table_id=table.id, status=EvalStatus.running, triggered_by="promotion")
        session.add(run)
        session.commit()
        session.refresh(run)
        
        print(f"Testing promotion for {table.name} with run {run.id}")
        
        try:
            promote_table_to_production_workflow(table.id, run.id)
            print("Completed workflow!")
            
            # verify
            session.refresh(run)
            print("Final run status:", run.status)
        except Exception as e:
            print("EXCEPTION in workflow!")
            traceback.print_exc()

if __name__ == "__main__":
    main()
