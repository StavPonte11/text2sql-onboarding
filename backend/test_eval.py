import sys
from sqlmodel import Session
from app.db.engine import engine
from app.models.models import Table
from app.services.evaluator import TextToSQLEvaluator

def main():
    with Session(engine) as session:
        question_scores = []
        evaluator = TextToSQLEvaluator(
            run_name=f"test-PhaseA",
            session=session,
            table_id="production-baseline",
            run_id="baseline",
            question_scores=question_scores,
        )
        try:
            print("Running single dataset...")
            res = evaluator.run_single_dataset("text2sql_production")
            print("Result length:", len(res) if res else "None")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    main()
