"""Migracje bazy (Alembic). Uruchamiane automatycznie przy starcie programu (app.migrate)."""
from alembic import context

from app import models  # noqa: F401  (rejestruje tabele w Base.metadata)
from app.db import Base, engine

target_metadata = Base.metadata


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        with engine.connect() as connection:
            _run(connection)
            connection.commit()
    else:
        _run(connection)


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
