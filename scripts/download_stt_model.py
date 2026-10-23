"""Explicitly download a faster-whisper model into the local model cache."""

import argparse

from faster_whisper import WhisperModel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="tiny")
    args = parser.parse_args()
    print(
        f"Downloading faster-whisper {args.model!r} into the local "
        "Hugging Face cache. This command may use network and disk space."
    )
    WhisperModel(
        args.model,
        device="cpu",
        compute_type="int8",
        local_files_only=False,
    )
    print("Model is ready in the local cache.")


if __name__ == "__main__":
    main()
