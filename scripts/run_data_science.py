
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data/processed/market_prices.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/data_workflow")
    parser.add_argument("--horizons", type=int, nargs="+", default=[1, 7, 30, 90])
    parser.add_argument("--save-models", action="store_true")
    args = parser.parse_args()
    from price_monitor.analysis.pipeline import run_analysis
    run_analysis(args.data, args.output, tuple(args.horizons), args.save_models)
    print(f"Report: {(args.output / 'report.md').resolve()}")


if __name__ == "__main__":
    main()
