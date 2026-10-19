"""
scripts/gen_contracts.py

Write the dashboard API's OpenAPI document to contracts/openapi.json (sorted, indented, final newline). The
frontend's types are generated from that file, and a unit test fails when it drifts from the API.

    uv run python scripts/gen_contracts.py
"""

from __future__ import annotations

from pathlib import Path

from khadbot.api import openapi_document

CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "openapi.json"


def main() -> None:
    CONTRACT.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT.write_text(openapi_document(), encoding="utf-8", newline="\n")
    print(f"Wrote {CONTRACT}")


if __name__ == "__main__":
    main()
