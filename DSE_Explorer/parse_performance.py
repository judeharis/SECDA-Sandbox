#!/usr/bin/env python3
"""Parse run log files for HWC cycle counts and DMA metrics.

Scans dataset/runs/<run_id>/results/*.log and extracts:
- HWC[28] cycle count (read)
- HWC[52] cycle count (compute)
- HWC[76] cycle count (write)
- fpga_total
- DMA data (transferred, recv, send/recv speed, wait times, counts)

Writes performance_summary.csv in the dataset directory.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Dict, Iterable

HWC_RE = re.compile(
    r"HWC\[(?P<idx>\d+)\]\s*\|\s*Current State:\s*\d+\s*\|\s*Cycle Count:\s*(?P<count>\d+)"
)
FPGA_TOTAL_RE = re.compile(r"^fpga_total:\s*(?P<value>\d+)")
DRIVER_RE = re.compile(r"^driver:\s*(?P<value>\d+)")

DMA_RE = re.compile(r"^Data Transfered(?P<recv> Recv)?:\s*(?P<value>\d+)\s*bytes")
SEND_WAIT_RE = re.compile(r"^send_wait:\s*(?P<value>\d+)")
RECV_WAIT_RE = re.compile(r"^recv_wait:\s*(?P<value>\d+)")
SEND_SPEED_RE = re.compile(r"^Send speed:\s*(?P<value>[-+]?\d+(?:\.\d+)?)\s*MB/s")
RECV_SPEED_RE = re.compile(r"^Recv speed:\s*(?P<value>[-+]?\d+(?:\.\d+)?)\s*MB/s")
SEND_COUNT_RE = re.compile(r"^Data Send Count:\s*(?P<value>\d+)")
RECV_COUNT_RE = re.compile(r"^Data Recv Count:\s*(?P<value>\d+)")
SEND_PER_RE = re.compile(r"^Data per Send:\s*(?P<value>\d+)\s*bytes")
RECV_PER_RE = re.compile(r"^Data per Recv:\s*(?P<value>\d+)\s*bytes")
VALIDATION_RE = re.compile(r"^Validation:\s*(?P<value>\w+)")


def parse_log(path: Path) -> Dict[str, str]:
    metrics: Dict[str, str] = {}
    if not path.exists():
        return metrics

    text = path.read_text(errors="ignore")
    for line in text.splitlines():
        hwc = HWC_RE.search(line)
        if hwc:
            idx = hwc.group("idx")
            hwc_id = (int(idx) - 28) // 24 + 1
            metrics[f"hwc_{hwc_id}_cycles"] = hwc.group("count")
            continue

        fpga = FPGA_TOTAL_RE.match(line)
        if fpga:
            metrics["fpga_total"] = fpga.group("value")
            continue

        driver = DRIVER_RE.match(line)
        if driver:
            metrics["driver"] = driver.group("value")
            continue
        
        validation = VALIDATION_RE.match(line)
        if validation:
            metrics["validation"] = validation.group("value")
            continue

        dma = DMA_RE.match(line)
        if dma:
            if dma.group("recv"):
                metrics["dma_transferred_recv_bytes"] = dma.group("value")
            else:
                metrics["dma_transferred_bytes"] = dma.group("value")
            continue

        for regex, key in [
            (SEND_WAIT_RE, "dma_send_wait"),
            (RECV_WAIT_RE, "dma_recv_wait"),
            (SEND_SPEED_RE, "dma_send_speed_mb_s"),
            (RECV_SPEED_RE, "dma_recv_speed_mb_s"),
            (SEND_COUNT_RE, "dma_send_count"),
            (RECV_COUNT_RE, "dma_recv_count"),
            (SEND_PER_RE, "dma_data_per_send_bytes"),
            (RECV_PER_RE, "dma_data_per_recv_bytes"),
        ]:
            match = regex.match(line)
            if match:
                metrics[key] = match.group("value")
                break

    return metrics


def collect_run_metrics(run_dir: Path) -> Dict[str, str]:
    metrics: Dict[str, str] = {"run_id": run_dir.name}
    results_dir = run_dir / "results"
    if not results_dir.exists():
        return metrics

    log_files = sorted(results_dir.glob("*.log"))
    if not log_files:
        return metrics

    # Use first log file (or merge if needed)
    metrics.update(parse_log(log_files[0]))
    metrics["log_file"] = log_files[0].name
    return metrics


def all_metric_keys(rows: Iterable[Dict[str, str]]) -> list[str]:
    keys = {"run_id", "log_file"}
    for row in rows:
        keys.update(row.keys())
    ordered = ["run_id", "log_file"] + sorted(
        k for k in keys if k not in {"run_id", "log_file"}
    )
    return ordered


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse HWC cycle counts and DMA stats into CSV"
    )
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
        help="Output CSV path (defaults to <dataset>/performance_summary.csv)",
    )
    args = parser.parse_args()

    dataset_dir = args.dataset
    runs_dir = dataset_dir / "runs"
    if not runs_dir.exists():
        raise SystemExit(f"Runs directory not found: {runs_dir}")

    rows = [
        collect_run_metrics(run_dir)
        for run_dir in sorted(runs_dir.iterdir())
        if run_dir.is_dir()
    ]
    if not rows:
        raise SystemExit(f"No runs found under {runs_dir}")

    output_path = args.output or (dataset_dir / "performance_summary.csv")
    fieldnames = all_metric_keys(rows)

    with output_path.open("w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
