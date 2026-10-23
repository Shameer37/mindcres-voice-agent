"""Apply the application's deterministic answer parser to paired STT transcripts."""

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.websocket import fast_extract_answer


EXPECTED_FIELDS = {
    1: "identity_confirmed",
    2: "issue_resolved",
    3: "still_facing",
    4: "technician_on_time",
    5: "technician_on_time",
    6: "technician_rating",
    7: "nps",
    8: "still_facing",
    9: "other_feedback",
    10: "other_feedback",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/stt_comparison.csv")
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "data/stt_comparison_answer_impact.csv",
    )
    args = parser.parse_args()

    with args.input.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    if len(rows) != 10:
        raise SystemExit(f"Expected the checked-in ten-utterance set, got {len(rows)}")

    for row in rows:
        number = int(row["utterance"])
        field = EXPECTED_FIELDS[number]
        row["expected_field"] = field
        values = []
        for label, transcript_key in (
            ("8khz", "transcript_8khz_ulaw"),
            ("16khz", "transcript_16khz_pcm"),
        ):
            extracted = fast_extract_answer(row[transcript_key], field)
            value = extracted.get(field) if extracted is not None else None
            row[f"{label}_extracted_value"] = repr(value)
            values.append(value)
        row["answer_changed"] = str(values[0] != values[1]).lower()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    changes = sum(row["answer_changed"] == "true" for row in rows)
    unresolved = sum(
        row[f"{rate}_extracted_value"] == "None"
        for row in rows for rate in ("8khz", "16khz")
    )
    print(f"rows={len(rows)} answer_changes={changes} unresolved_extractions={unresolved}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
