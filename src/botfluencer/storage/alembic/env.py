from alembic import context
from sqlalchemy import create_engine, pool, text

from botfluencer.storage.models import Base

config = context.config
url = config.attributes["database_url"]


def run() -> None:
    engine = create_engine(
        url,
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000"},
    )
    with engine.connect() as connection:
        # Serialize migrations without sharing the worker's namespace lock.
        connection.execute(text("SELECT pg_advisory_lock(7134202601)"))
        connection.commit()
        try:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(7134202601)"))
            connection.commit()
    engine.dispose()


run()
