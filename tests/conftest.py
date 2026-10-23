import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Customer


@pytest.fixture
def test_session_factory():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(bind=test_engine)

    factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
    )

    yield factory

    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


@pytest.fixture
def client(test_session_factory):
    def override_get_db():
        db = test_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def test_customer(test_session_factory):
    with test_session_factory() as db:
        customer = Customer(
            customer_id="TEST001",
            name="Test Customer",
            mobile="0000000000",
            preferred_language="en",
            ticket_id="TESTTKT001",
            issue_type="Internet outage",
            technician_name="Test Technician",
            visit_date="2026-10-20",
            promised_slot="09:00-11:00",
        )
        db.add(customer)
        db.commit()

    return "TEST001"
