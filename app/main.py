
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database import Base, engine, get_db
from app.models import Call, Customer
from app.schemas import (
    CallCreate,
    CallDetailRead,
    CallRead,
    CustomerRead,
)
from app.services.call_service import create_call
from app.api.websocket import router as websocket_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create missing tables at startup.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
)

# Register the existing WebSocket routes.
app.include_router(websocket_router)


def mask_mobile(mobile: str) -> str:
    """Return a masked representation instead of exposing the number."""
    return "*" * max(0, len(mobile) - 4) + mobile[-4:]


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.app_env,
    }


@app.get("/customers", response_model=list[CustomerRead])
def list_customers(db: Session = Depends(get_db)):
    customers = db.scalars(
        select(Customer).order_by(Customer.customer_id)
    ).all()

    return [
        {
            "customer_id": customer.customer_id,
            "name": customer.name,
            "mobile_masked": mask_mobile(customer.mobile),
            "preferred_language": customer.preferred_language,
            "ticket_id": customer.ticket_id,
            "issue_type": customer.issue_type,
            "technician_name": customer.technician_name,
            "visit_date": customer.visit_date,
            "promised_slot": customer.promised_slot,
        }
        for customer in customers
    ]


@app.post("/calls", response_model=CallRead, status_code=201)
def start_call(
    payload: CallCreate,
    db: Session = Depends(get_db),
):
    try:
        return create_call(db, payload.customer_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/calls", response_model=list[CallRead])
def list_calls(
    outcome: str | None = Query(default=None),
    needs_followup: bool | None = Query(default=None),
    db: Session = Depends(get_db),
):
    query = select(Call).order_by(Call.created_at.desc())

    if outcome is not None:
        query = query.where(Call.outcome == outcome)

    if needs_followup is not None:
        query = query.where(Call.needs_followup == needs_followup)

    return db.scalars(query).all()


@app.get("/calls/{call_id}", response_model=CallDetailRead)
def get_call(
    call_id: str,
    db: Session = Depends(get_db),
):
    query = (
        select(Call)
        .options(
            selectinload(Call.transcript),
            selectinload(Call.turn_metrics),
        )
        .where(Call.id == call_id)
    )
    call = db.scalar(query)

    if call is None:
        raise HTTPException(status_code=404, detail="Call not found.")

    return call


# Serve the browser phone simulator.
# Keep this mount AFTER all API routes so it does not shadow them.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

if WEB_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=str(WEB_DIR), html=True),
        name="web",
    )
