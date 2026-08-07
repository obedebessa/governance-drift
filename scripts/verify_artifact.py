#!/usr/bin/env python3
"""Re-execute the governance-drift study and verify canonical outputs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUTPUTS = (
    "detector_raw.csv",
    "matrix.csv",
    "fp_sensitivity.csv",
    "table_matrix_full.tex",
)


def main() -> int:
    before = {name: (DATA / name).read_bytes() for name in OUTPUTS}
    subprocess.run([sys.executable, "code/detector_study.py"], cwd=ROOT, check=True)
    for name in OUTPUTS:
        if (DATA / name).read_bytes() != before[name]:
            raise SystemExit(f"canonical output changed: data/{name}")
    subprocess.run([sys.executable, "scripts/verify_lab_results.py"], cwd=ROOT, check=True)
    print("PASS: modeled and live-laboratory artifacts verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
