#!/usr/bin/env python3
"""Parse HLS csynth.xml reports into a CSV summary.

Scans dataset/runs/<run_id>/results/csynth.xml and extracts key timing/resource fields.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, Iterable
import xml.etree.ElementTree as ET


def safe_parse_xml(path: Path) -> ET.Element | None:
    try:
        tree = ET.parse(path)
        return tree.getroot()
    except (FileNotFoundError, ET.ParseError, OSError):
        return None


def xml_text(root: ET.Element, xpath: str) -> str | None:
    node = root.find(xpath)
    if node is None or node.text is None:
        return None
    text = node.text.strip()
    return text if text else None


def parse_csynth(path: Path) -> Dict[str, str]:
    metrics: Dict[str, str] = {}
    root = safe_parse_xml(path)
    if root is None:
        return metrics

    direct_fields = {
        "hls_version": "./ReportVersion/Version",
        "target_clock_period_ns": "./UserAssignments/TargetClockPeriod",
        "clock_uncertainty_ns": "./UserAssignments/ClockUncertainty",
        "estimated_clock_period_ns": "./PerformanceEstimates/SummaryOfTimingAnalysis/EstimatedClockPeriod",
        "part": "./UserAssignments/Part",
        "product_family": "./UserAssignments/ProductFamily",
        "top_model": "./UserAssignments/TopModelName",
    }

    for key, xpath in direct_fields.items():
        value = xml_text(root, xpath)
        if value is not None:
            metrics[key] = value

    for res in ["BRAM_18K", "DSP48E", "FF", "LUT", "URAM"]:
        used = xml_text(root, f"./AreaEstimates/Resources/{res}")
        avail = xml_text(root, f"./AreaEstimates/AvailableResources/{res}")
        if used is not None:
            metrics[f"{res.lower()}_used"] = used
        if avail is not None:
            metrics[f"{res.lower()}_avail"] = avail
        if used is not None and avail is not None:
            try:
                used_f = float(used)
                avail_f = float(avail)
                if avail_f > 0:
                    metrics[f"{res.lower()}_util_pct"] = f"{(used_f / avail_f) * 100:.2f}"
            except ValueError:
                pass

    return metrics


def collect_run_metrics(run_dir: Path) -> Dict[str, str]:
    results_dir = run_dir / "results"
    metrics: Dict[str, str] = {"run_id": run_dir.name}
    metrics.update(parse_csynth(results_dir / "csynth.xml"))
    return metrics


def all_metric_keys(rows: Iterable[Dict[str, str]]) -> list[str]:
    keys = {"run_id"}
    for row in rows:
        keys.update(row.keys())
    return ["run_id"] + sorted(k for k in keys if k != "run_id")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse HLS csynth.xml files into hls_summary.csv")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Path to dataset folder",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path (defaults to <dataset>/hls_summary.csv)",
    )
    args = parser.parse_args()

    dataset_dir = args.dataset
    runs_dir = dataset_dir / "runs"
    if not runs_dir.exists():
        raise SystemExit(f"Runs directory not found: {runs_dir}")

    rows = [collect_run_metrics(run_dir) for run_dir in sorted(runs_dir.iterdir()) if run_dir.is_dir()]
    if not rows:
        raise SystemExit(f"No runs found under {runs_dir}")

    output_path = args.output or (dataset_dir / "hls_summary.csv")
    fieldnames = all_metric_keys(rows)

    with output_path.open("w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
