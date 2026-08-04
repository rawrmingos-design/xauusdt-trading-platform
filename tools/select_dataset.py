#!/usr/bin/env python3
"""Select a frozen dataset version and emit backtest-ready range metadata.

Does not modify strategy configs. Writes selection pointer under docs/datasets/.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = REPO_ROOT / "docs" / "datasets"
SELECTION_PATH = DATASET_DIR / "SELECTED.json"


def load_manifest(version: str) -> dict:
    path = DATASET_DIR / f"{version}.json"
    if not path.exists():
        # allow bare stem without ds_ prefix mistakes
        matches = list(DATASET_DIR.glob(f"*{version}*.json"))
        if len(matches) == 1:
            path = matches[0]
        else:
            available = [p.stem for p in sorted(DATASET_DIR.glob("*.json"))]
            raise SystemExit(f"Manifest not found for {version!r}. Available: {available}")
    return json.loads(path.read_text()), path


def main() -> None:
    parser = argparse.ArgumentParser(description="Select frozen dataset for backtests")
    parser.add_argument("--version", required=True, help="e.g. ds_xau_15m_365d_v1")
    parser.add_argument(
        "--print-env",
        action="store_true",
        help="Print shell exports for start/end/symbol/granularity",
    )
    args = parser.parse_args()

    data, path = load_manifest(args.version)
    selection = {
        "selected_at": datetime.now().astimezone().isoformat(),
        "version_id": data.get("version_id"),
        "manifest_path": str(path.relative_to(REPO_ROOT)),
        "symbol": data.get("symbol"),
        "granularity": data.get("granularity"),
        "start_time": data.get("start_time"),
        "end_time": data.get("end_time"),
        "candle_count": data.get("candle_count"),
        "fingerprint": data.get("fingerprint"),
        "periods": data.get("periods"),
        "note": (
            "Logical selection only. Backtests should filter candles to "
            "[start_time, end_time) from the shared candle store."
        ),
    }
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    SELECTION_PATH.write_text(json.dumps(selection, indent=2) + "\n")

    print(f"Selected {selection['version_id']}")
    print(f"  manifest: {selection['manifest_path']}")
    print(f"  range:    {selection['start_time']} → {selection['end_time']}")
    print(f"  candles:  {selection['candle_count']}")
    print(f"  fingerprint: {selection['fingerprint']}")
    print(f"  pointer:  {SELECTION_PATH.relative_to(REPO_ROOT)}")

    if args.print_env:
        print()
        print(f"export DATASET_VERSION={selection['version_id']}")
        print(f"export DATASET_SYMBOL={selection['symbol']}")
        print(f"export DATASET_GRANULARITY={selection['granularity']}")
        print(f"export DATASET_START={selection['start_time']}")
        print(f"export DATASET_END={selection['end_time']}")


if __name__ == "__main__":
    main()
