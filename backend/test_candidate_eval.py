import sys
from sqlmodel import Session, select
from app.db.engine import engine
from app.models.models import Table, GoldenQuestion
from app.routers.evaluation import _run_candidate_eval

def main():
    with Session(engine) as session:
        # find a table with golden questions
        table = session.exec(select(Table)).first()
        questions = session.exec(select(GoldenQuestion).where(GoldenQuestion.table_id == table.id)).all()
        if not questions:
            print(f"Table {table.name} has no questions.")
            return

        run_name_prefix = "Promo-test"
        try:
            print(f"Running candidate eval for {table.name}...")
            score = _run_candidate_eval(table, questions, run_name_prefix, session, "test-promo-id")
            print(f"Candidate score: {score}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    main()
