#!/usr/bin/env python3
"""Parse DSE dataset result reports into a CSV summary.

Scans dataset/runs/<run_id>/results and extracts metrics from:
- timing_report_impl_full.txt
- timing_report_impl_ip.txt
- utilization_report_impl_full.txt
- utilization_report_impl_ip.txt

Outputs a CSV where each metric is prefixed with the source filename.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Dict, Iterable

TIMING_FILES = [
    "timing_report_impl_full.txt",
    "timing_report_impl_ip.txt",
]
UTIL_FILES = [
    "utilization_report_impl_full.txt",
    "utilization_report_impl_ip.txt",
]

TIMING_SUMMARY_RE = re.compile(
    r"^\s*(?P<wns>[-+]?\d+(?:\.\d+)?)\s+"
    r"(?P<tns>[-+]?\d+(?:\.\d+)?)\s+"
    r"(?P<tns_fail>\d+)\s+"
    r"(?P<tns_total>\d+)\s+"
    r"(?P<whs>[-+]?\d+(?:\.\d+)?)\s+"
    r"(?P<ths>[-+]?\d+(?:\.\d+)?)\s+"
    r"(?P<ths_fail>\d+)\s+"
    r"(?P<ths_total>\d+)\s+"
    r"(?P<wpws>[-+]?\d+(?:\.\d+)?)\s+"
    r"(?P<tpws>[-+]?\d+(?:\.\d+)?)\s+"
    r"(?P<tpws_fail>\d+)\s+"
    r"(?P<tpws_total>\d+)\s*$"
)

CLOCK_SUMMARY_RE = re.compile(
    r"^\s*(?P<clock>\S+)\s+\{(?P<waveform>[^}]+)\}\s+"
    r"(?P<period>[-+]?\d+(?:\.\d+)?)\s+(?P<freq>[-+]?\d+(?:\.\d+)?)\s*$"
)

UTIL_ROW_RE = re.compile(
    r"^\|\s*(?P<label>[^|]+?)\s*\|\s*(?P<used>\d+)\s*\|\s*(?P<fixed>\d+)\s*\|\s*(?P<avail>\d+)\s*\|\s*(?P<util>[0-9.]+)\s*\|\s*$"
)

UTIL_LABELS = {
    "Slice LUTs": "slice_luts",
    "Slice Registers": "slice_registers",
    "Block RAM Tile": "bram_tiles",
    "DSPs": "dsps",
}


def safe_read(path: Path) -> str:
    try:
        return path.read_text(errors="ignore")
    except FileNotFoundError:
        return ""


def parse_timing_report(path: Path) -> Dict[str, str]:
    metrics: Dict[str, str] = {}
    text = safe_read(path)
    if not text:
        return metrics

    lines = text.splitlines()

    # Design Timing Summary table: find the header then parse next numeric line
    for idx, line in enumerate(lines):
        if "Design Timing Summary" in line:
            # find the next line that matches the summary regex
            for j in range(idx, min(idx + 20, len(lines))):
                match = TIMING_SUMMARY_RE.match(lines[j])
                if match:
                    metrics.update(match.groupdict())
                    break
            break

    # Clock Summary: take first clock line after header
    for idx, line in enumerate(lines):
        if line.strip().startswith("Clock Summary"):
            for j in range(idx, min(idx + 15, len(lines))):
                match = CLOCK_SUMMARY_RE.match(lines[j])
                if match:
                    metrics["clock_name"] = match.group("clock")
                    metrics["clock_period_ns"] = match.group("period")
                    metrics["clock_freq_mhz"] = match.group("freq")
                    break
            break

    return metrics


def parse_util_report(path: Path) -> Dict[str, str]:
    metrics: Dict[str, str] = {}
    text = safe_read(path)
    if not text:
        return metrics

    for line in text.splitlines():
        match = UTIL_ROW_RE.match(line)
        if not match:
            continue
        label = match.group("label").strip()
        if label in UTIL_LABELS:
            key = UTIL_LABELS[label]
            metrics[f"{key}_used"] = match.group("used")
            metrics[f"{key}_avail"] = match.group("avail")
            metrics[f"{key}_util_pct"] = match.group("util")

    return metrics


def prefixed(metrics: Dict[str, str], prefix: str) -> Dict[str, str]:
    return {f"{prefix}.{key}": value for key, value in metrics.items()}


def collect_run_metrics(run_dir: Path) -> Dict[str, str]:
    results_dir = run_dir / "results"
    metrics: Dict[str, str] = {"run_id": run_dir.name}

    for fname in TIMING_FILES:
        metrics.update(prefixed(parse_timing_report(results_dir / fname), fname))

    for fname in UTIL_FILES:
        metrics.update(prefixed(parse_util_report(results_dir / fname), fname))

    return metrics


def all_metric_keys(rows: Iterable[Dict[str, str]]) -> list[str]:
    keys = {"run_id"}
    for row in rows:
        keys.update(row.keys())
    ordered = ["run_id"] + sorted(k for k in keys if k != "run_id")
    return ordered


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse result reports into a CSV summary")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Path to dataset folder (defaults to script directory)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path (defaults to <dataset>/results_summary.csv)",
    )
    args = parser.parse_args()

    dataset_dir = args.dataset
    runs_dir = dataset_dir / "runs"
    if not runs_dir.exists():
        raise SystemExit(f"Runs directory not found: {runs_dir}")

    rows = [collect_run_metrics(run_dir) for run_dir in sorted(runs_dir.iterdir()) if run_dir.is_dir()]
    if not rows:
        raise SystemExit(f"No runs found under {runs_dir}")

    output_path = args.output or (dataset_dir / "results_summary.csv")
    fieldnames = all_metric_keys(rows)

    with output_path.open("w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
