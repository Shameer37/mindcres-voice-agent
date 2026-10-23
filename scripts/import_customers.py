import csv
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import Base, SessionLocal, engine
from app.models import Customer


CSV_PATH = PROJECT_ROOT / "data" / "call_list.csv"

REQUIRED_COLUMNS = {
    "customer_id",
    "name",
    "mobile",
    "preferred_language",
    "ticket_id",
    "issue_type",
    "technician_name",
    "visit_date",
    "promised_slot",
}
VALID_LANGUAGES = {"en", "hi", "te"}


def load_csv_rows() -> list[dict[str, str]]:
    """Read and validate the required CSV structure."""
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"CSV file not found: {CSV_PATH}")

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        if not reader.fieldnames:
            raise ValueError("The CSV file has no header row.")

        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"CSV is missing required columns: {sorted(missing)}"
            )

        rows = list(reader)

    if len(rows) < 10:
        raise ValueError("The assignment requires at least 10 customers.")

    seen_ids: set[str] = set()

    for line_number, row in enumerate(rows, start=2):
        for column in REQUIRED_COLUMNS:
            if not (row.get(column) or "").strip():
                raise ValueError(
                    f"CSV line {line_number}: '{column}' cannot be empty."
                )

        customer_id = row["customer_id"].strip()
        if customer_id in seen_ids:
            raise ValueError(
                f"CSV line {line_number}: duplicate customer_id."
            )
        seen_ids.add(customer_id)

        language = row["preferred_language"].strip().lower()
        if language not in VALID_LANGUAGES:
            raise ValueError(
                f"CSV line {line_number}: language must be en, hi or te."
            )

    return rows


def import_customers() -> int:
    """Insert new customers and update existing customer records."""
    Base.metadata.create_all(bind=engine)
    rows = load_csv_rows()
    imported = 0

    with SessionLocal() as db:
        for row in rows:
            customer_id = row["customer_id"].strip()
            customer = db.get(Customer, customer_id)

            if customer is None:
                customer = Customer(customer_id=customer_id)
                db.add(customer)
                imported += 1

            customer.name = row["name"].strip()
            customer.mobile = row["mobile"].strip()
            customer.preferred_language = (
                row["preferred_language"].strip().lower()
            )
            customer.ticket_id = row["ticket_id"].strip()
            customer.issue_type = row["issue_type"].strip()
            customer.technician_name = row["technician_name"].strip()
            customer.visit_date = row["visit_date"].strip()
            customer.promised_slot = row["promised_slot"].strip()

        db.commit()

    return imported


if __name__ == "__main__":
    count = import_customers()
    print(f"Customer import complete. New records added: {count}")
