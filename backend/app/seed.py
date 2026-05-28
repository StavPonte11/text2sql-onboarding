from sqlmodel import Session, select

from app.db.engine import create_db_and_tables, engine
from app.models.models import (
    DifficultyLevel,
    EnrichmentVersion,
    EvalResult,
    EvalRun,
    EvalStatus,
    GoldenQuestion,
    Table,
    TableStatus,
    UserScope,
)


def seed():
    # Run migrations to ensure all schemas and tables exist
    create_db_and_tables()

    with Session(engine) as session:
        # Check if already seeded
        if session.exec(select(Table)).first():
            print("Database already seeded.")
            return

        print("Seeding database...")

        # 1. Scopes
        scope1 = UserScope(user_id="user-1", name="Finance Tables", is_active=True)
        scope2 = UserScope(user_id="user-1", name="Marketing Tables", is_active=False)
        session.add_all([scope1, scope2])

        # 2. Tables
        t1 = Table(name="orders", schema_name="tiny", status=TableStatus.production,oasis_source_id="1",catalog="tpch",service="local_trino", owner_id="user-1")
        t2 = Table(name="customer", schema_name="tiny", status=TableStatus.production,oasis_source_id="2",owner_id="user-1",catalog="tpch",service="local_trino")
        t3 = Table(name="lineitem", schema_name="tiny", status=TableStatus.sandbox, oasis_source_id="3", owner_id="user-2",catalog="tpch",service="local_trino")
        session.add_all([t1, t2, t3])
        session.flush()

        # 3. Enrichment
        e1 = EnrichmentVersion(
            table_id=t1.id,
            version=1,
            data={
                "table_description": "Contains all finalized e-commerce orders from the TPC-H dataset.",
                "columns": [
                    {"name": "orderkey", "description": "Primary key for the order. Unique identifier.", "is_geo": False, "is_time": False},
                    {"name": "custkey", "description": "Foreign key to the customers table.", "is_geo": False, "is_time": False},
                    {"name": "totalprice", "description": "Total order amount.", "is_geo": False, "is_time": False},
                    {"name": "orderdate", "description": "Timestamp when the order was placed.", "is_geo": False, "is_time": True}
                ]
            }
        )
        session.add(e1)

        # 4. Golden Questions
        q1 = GoldenQuestion(table_id=t1.id, question="How many orders were placed last month?", expected_sql="SELECT count(*) FROM orders WHERE created_at >= date_trunc('month', current_date - interval '1 month') AND created_at < date_trunc('month', current_date);", difficulty=DifficultyLevel.simple)
        q2 = GoldenQuestion(table_id=t1.id, question="What is the total revenue by customer?", expected_sql="SELECT customer_id, sum(amount) FROM orders GROUP BY customer_id;", difficulty=DifficultyLevel.medium)
        session.add_all([q1, q2])
        session.flush()

        # 5. Eval Runs
        run1 = EvalRun(table_id=t1.id, score=1.0, status=EvalStatus.completed)
        session.add(run1)
        session.flush()

        # 6. Eval Results
        res1 = EvalResult(run_id=run1.id, question_id=q1.id, score=1.0, status="pass")
        res2 = EvalResult(run_id=run1.id, question_id=q2.id, score=1.0, status="pass")
        session.add_all([res1, res2])

        session.commit()
        print("Database seeded successfully.")

if __name__ == "__main__":
    seed()
