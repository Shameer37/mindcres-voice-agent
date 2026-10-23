from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# Resolve relative SQLite paths from the project root.
database_url = settings.database_url

if database_url.startswith("sqlite:///./"):
    relative_path = database_url.removeprefix("sqlite:///./")
    absolute_path = (PROJECT_ROOT / relative_path).resolve()
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    database_url = f"sqlite:///{absolute_path.as_posix()}"


connect_args = (
    {"check_same_thread": False}
    if database_url.startswith("sqlite")
    else {}
)

engine = create_engine(
    database_url,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy database models."""


def get_db():
    """Provide one database session per API request."""
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()