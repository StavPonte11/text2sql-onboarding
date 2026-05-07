import logging
logging.basicConfig(level=logging.INFO)
from app.db.engine import engine
from sqlmodel import Session, select
from app.models.models import Table
from app.config import settings
from app.services.profiling_engine import run_table_profiling

with Session(engine) as session:
    tables = session.exec(select(Table)).all()
    if tables:
        t = tables[0]
        try:
            res = run_table_profiling(t.id, settings.TRINO_CATALOG, t.schema_name, t.name)
            print("Success!", res.success)
        except Exception as e:
            print("EXCEPTION:", repr(e))
