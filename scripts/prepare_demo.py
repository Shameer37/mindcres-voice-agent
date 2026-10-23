"""Explicitly prepare local model weights and fictional demo customers."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from faster_whisper import WhisperModel

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.import_customers import import_customers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.getenv("STT_MODEL_SIZE", "tiny"))
    args = parser.parse_args()
    print(
        f"Preparing faster-whisper {args.model!r}. If it is not cached, "
        "this explicit setup command downloads model weights."
    )
    WhisperModel(
        args.model,
        device=os.getenv("STT_DEVICE", "cpu"),
        compute_type=os.getenv("STT_COMPUTE_TYPE", "int8"),
        local_files_only=False,
    )
    imported = import_customers()
    print(f"Model ready. Fictional customer records added: {imported}.")


if __name__ == "__main__":
    main()
