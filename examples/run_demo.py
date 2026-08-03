#!/usr/bin/env python3
"""Deterministic two-node offline demo entry point (§12).

Usage:
    python examples/run_demo.py [--workdir DIR]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from federated_agent_web.demo import run_demo  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="FAW offline two-node demo")
    parser.add_argument("--workdir", help="scratch directory (default: temp)")
    args = parser.parse_args()
    return run_demo(Path(args.workdir) if args.workdir else None)


if __name__ == "__main__":
    sys.exit(main())
