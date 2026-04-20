from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


SQLITE_STARTUP_MIGRATIONS = {
    ("support_requests", "completion_note"): "TEXT",
    ("user_push_tokens", "installation_id"): "TEXT",
}

SQLITE_STARTUP_INDEXES = {
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_user_push_tokens_installation_id ON user_push_tokens (installation_id)",
}


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, database_url: str):
        self.engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False}
            if database_url.startswith("sqlite")
            else {},
        )
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        self._reconcile_sqlite_schema()

    def _reconcile_sqlite_schema(self) -> None:
        if self.engine.dialect.name != "sqlite":
            return

        inspector = inspect(self.engine)
        existing_tables = set(inspector.get_table_names())

        with self.engine.begin() as connection:
            for (table_name, column_name), column_type in SQLITE_STARTUP_MIGRATIONS.items():
                if table_name not in existing_tables:
                    continue

                existing_columns = {
                    column["name"] for column in inspector.get_columns(table_name)
                }
                if column_name in existing_columns:
                    continue

                connection.execute(
                    text(
                        f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_type}'
                    )
                )

            for index_statement in SQLITE_STARTUP_INDEXES:
                connection.execute(text(index_statement))

    def session(self) -> Generator[Session, None, None]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()
