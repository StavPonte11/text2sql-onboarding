import sys
import logging

logging.basicConfig(level=logging.INFO)

sys.path.insert(0, "/app/backend")

from core.services.profiling_engine import run_table_profiling
from core.db.engine import engine
from sqlmodel import Session, select
from core.models.models import Table

def run():
    with Session(engine) as session:
        table = session.exec(select(Table)).first()
        
    if not table:
        print("No tables found")
        return
        
    print(f"Profiling {table.catalog}.{table.schema_name}.{table.name}")
    
    result = run_table_profiling(
        table_id=str(table.id),
        catalog=table.catalog,
        schema=table.schema_name,
        table=table.name,
        version=1
    )
    
    print(f"Success: {result.success}")
    print(f"Row count: {result.row_count}")
    print(f"Column count: {result.column_count}")
    
    if hasattr(result, 'auto_insights'):
        print("auto_insights STILL EXISTS!")
    else:
        print("auto_insights successfully removed!")

if __name__ == "__main__":
    run()
