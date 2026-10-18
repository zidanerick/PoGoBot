#!/usr/bin/env python3
"""Print a deterministic, non-actuating straight gesture plan as JSON."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pogobot.gestures import Calibration, plan_straight  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen", nargs=2, type=int, default=(1080, 2340),
                        metavar=("WIDTH", "HEIGHT"))
    parser.add_argument("--start", nargs=2, type=float, default=(.5, .84),
                        metavar=("X", "Y"))
    parser.add_argument("--end", nargs=2, type=float, default=(.5, .38),
                        metavar=("X", "Y"))
    parser.add_argument("--duration-ms", type=int, default=240)
    parser.add_argument("--interval-ms", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    plan = plan_straight(tuple(args.start), tuple(args.end),
                         duration_ms=args.duration_ms,
                         interval_ms=args.interval_ms, seed=args.seed)
    print(json.dumps(plan.trace(Calibration(tuple(args.screen))), indent=2))


if __name__ == "__main__":
    main()
