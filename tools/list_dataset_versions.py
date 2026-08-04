#!/usr/bin/env python3
"""List frozen dataset versions under docs/datasets/."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = REPO_ROOT / "docs" / "datasets"


def main() -> None:
    parser = argparse.ArgumentParser(description="List frozen dataset manifests")
    parser.add_argument("--json", action="store_true", help="Raw JSON array output")
    args = parser.parse_args()

    if not DATASET_DIR.exists():
        print("No docs/datasets/ directory. Run tools/run_data_010_extend.py first.")
        return

    rows: list[dict] = []
    for path in sorted(DATASET_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        rows.append(
            {
                "version_id": data.get("version_id", path.stem),
                "symbol": data.get("symbol"),
                "granularity": data.get("granularity"),
                "start_time": data.get("start_time"),
                "end_time": data.get("end_time"),
                "candle_count": data.get("candle_count"),
                "gap_count": data.get("gap_count"),
                "duplicate_count": data.get("duplicate_count"),
                "fingerprint": data.get("fingerprint"),
                "path": str(path.relative_to(REPO_ROOT)),
            }
        )

    if args.json:
        print(json.dumps(rows, indent=2))
        return

    if not rows:
        print("No dataset manifests found.")
        return

    print(f"{'VERSION':<28} {'GRAN':<6} {'COUNT':>7} {'GAPS':>5} {'RANGE'}")
    print("-" * 90)
    for r in rows:
        start = (r.get("start_time") or "")[:10]
        end = (r.get("end_time") or "")[:10]
        print(
            f"{r['version_id']:<28} {str(r.get('granularity') or ''):<6} "
            f"{r.get('candle_count') or 0:>7} {r.get('gap_count') or 0:>5} "
            f"{start} → {end}"
        )
    print()
    print("Select: uv run python tools/select_dataset.py --version <VERSION>")


if __name__ == "__main__":
    main()
