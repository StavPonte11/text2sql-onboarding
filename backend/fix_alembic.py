from sqlalchemy import create_engine, text
engine = create_engine('postgresql://postgres:postgres@localhost:5432/text2sql')
with engine.connect() as conn:
    res = conn.execute(text("SELECT * FROM alembic_version"))
    for row in res:
        print("Current version:", row)
    
    conn.execute(text("UPDATE alembic_version SET version_num = '70e8a34ff877' WHERE version_num = 'a123e456b789'"))
    conn.commit()
    print("Updated to 70e8a34ff877")
