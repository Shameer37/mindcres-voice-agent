from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Call, Customer


def create_call(db: Session, customer_id: str) -> Call:
    """Create a call record for an existing customer."""
    customer = db.get(Customer, customer_id)

    if customer is None:
        raise LookupError("Customer not found.")

    call = Call(
        id=str(uuid4()),
        customer_id=customer.customer_id,
        status="created",
    )

    db.add(call)
    db.commit()
    db.refresh(call)

    return call