#!/usr/bin/env python3
"""Re-execute the governance-drift study and verify canonical outputs."""

from __future__ import annotations

import os
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
    "join_baseline.csv",
    "join_baseline_summary.json",
    "table_join_baseline.tex",
    "ablation_raw.csv",
    "ablation_probes.csv",
    "ablation_summary.json",
    "table_ablation.tex",
)


def main() -> int:
    before = {name: (DATA / name).read_bytes() for name in OUTPUTS}
    subprocess.run([sys.executable, "code/detector_study.py"], cwd=ROOT, check=True)
    subprocess.run(
        [sys.executable, "code/join_baseline_study.py"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": "code"},
        check=True,
    )
    subprocess.run(
        [sys.executable, "code/ablation_study.py"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": "code"},
        check=True,
    )
    for name in OUTPUTS:
        if (DATA / name).read_bytes() != before[name]:
            raise SystemExit(f"canonical output changed: data/{name}")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "-v",
            "lab/test_evaluator_contract.py",
            "lab/test_basis_selection.py",
            "lab/test_snapshot_integrity.py",
            "lab/test_ablation_study.py",
            "lab/test_batch_evaluator.py",
            "lab/test_evidence_gateway.py",
            "lab/test_image_lock.py",
            "lab/test_temporal_cut.py",
            "lab/test_live_fleet_adapter.py",
            "lab/test_trace_harness.py",
            "lab/test_cross_stack_adapter.py",
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": f"lab{os.pathsep}code"},
        check=True,
    )
    subprocess.run([sys.executable, "scripts/verify_lab_results.py"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "scripts/analyze_lab_extension.py"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "scripts/verify_transition_controls.py"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "scripts/analyze_scaling.py"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "scripts/analyze_faults.py"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "scripts/analyze_live_fleet.py"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "scripts/analyze_cross_stack.py"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "scripts/run_scoping_review.py"], cwd=ROOT, check=True)
    trace_campaign = (ROOT / "lab/results_trace/FINAL_CAMPAIGN").read_text().strip()
    if Path(trace_campaign).name != trace_campaign or not trace_campaign.startswith("trace-"):
        raise SystemExit(f"invalid trace campaign pointer: {trace_campaign!r}")
    trace_path = ROOT / "lab/results_trace" / trace_campaign
    subprocess.run(
        [sys.executable, "scripts/verify_trace_results.py", str(trace_path)],
        cwd=ROOT,
        check=True,
    )
    print("PASS: modeled and live-laboratory artifacts verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
