#!/usr/bin/env python3
"""Build the portable NANO AIRI character-card package."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "airi" / "nano"


def validate_source() -> None:
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    card = json.loads((SOURCE / "card.json").read_text(encoding="utf-8"))

    assert manifest["format"] == "airi-character-card"
    assert manifest["version"] == 1
    assert manifest["card"] == {"path": "card.json", "spec": "chara_card_v3"}
    assert card["spec"] == "chara_card_v3"
    assert card["spec_version"] == "3.0"
    assert card["data"]["name"] == "NANO"


def build(output: Path) -> Path:
    validate_source()
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(SOURCE / "manifest.json", "manifest.json")
        archive.write(SOURCE / "card.json", "card.json")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "NANO-AIRI-Card-v1.0.0.zip")
    args = parser.parse_args()
    print(build(args.output))


if __name__ == "__main__":
    main()
