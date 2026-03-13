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
from typing import Dict, Iterable, List, Set


def parse_filename(name: str) -> Dict[str, str]:
    """Parse identifiers from the SECDA log filename.

    If the filename doesn't match the expected pattern, we still return a stable
    dict with best-effort defaults.
    """

    # Supported shapes:
    #  1) legacy: <BOARD>_<EXP_NAME...>_<ACCELERATOR>.bit_<YYYY_MM_DD_hh_mm_ss>.log
    #     e.g. Z1_dma_exp_v2_DMA_EXP_Z1_2_1.bit_2026_02_06_14_59_02.log
    #  2) collected: <BOARD>_<EXP_NAME...>_<ACCELERATOR>_<YYYY_MM_DD_hh_mm_ss>.log
    #     e.g. Z1_dma_exp_v2_DMA_EXP_Z1_2_1_2026_02_06_15_25_54.log
    if not name.endswith(".log"):
        return {
            "board": "",
            "exp_name": "",
            "exp_version": "",
            "accelerator": "",
            "run_ts": "",
        }

    stem = name[: -len(".log")]

    # Determine how to split off the run timestamp:
    # - legacy has explicit ".bit_" marker
    # - collected form just has "_<timestamp>" at the end
    if ".bit_" in stem:
        left, run_ts = stem.rsplit(".bit_", 1)
    else:
        # Expect exactly 6 timestamp components at the end.
        parts_all = stem.split("_")
        if len(parts_all) < 1 + 6:
            return {
                "board": stem.split("_")[0] if "_" in stem else "",
                "exp_name": "",
                "exp_version": "",
                "accelerator": "",
                "run_ts": "",
            }
        run_ts = "_".join(parts_all[-6:])
        left = "_".join(parts_all[:-6])

    parts = left.split("_")
    if len(parts) < 2:
        return {
            "board": "",
            "exp_name": "",
            "exp_version": "",
            "accelerator": "",
            "run_ts": run_ts,
        }

    board = parts[0]

    # Find where the accelerator token starts (DMA_EXP / MM_EXP / AXIMM_TEST / ...).
    # We treat the first token that looks "constant-like" (contains an A-Z) as the start.
    acc_start = None
    for i, p in enumerate(parts[1:], start=1):
        if any("A" <= ch <= "Z" for ch in p):
            acc_start = i
            break

    if acc_start is None:
        # Can't reliably split exp vs accelerator; fall back to a simple heuristic:
        # treat the last two underscore groups as <major>_<minor> version if present,
        # and treat everything before that as exp_name.
        exp_name = "_".join(parts[1:])
        accelerator = ""
        exp_version = ""
        if len(parts) >= 4 and parts[-1].isdigit() and parts[-2].isdigit():
            exp_version = f"{parts[-2]}_{parts[-1]}"
        return {
            "board": board,
            "exp_name": exp_name,
            "exp_version": exp_version,
            "accelerator": accelerator,
            "run_ts": run_ts,
        }
        return {
            "board": board,
            "exp_name": "_".join(parts[1:-1]) if len(parts) > 2 else "",
            "exp_version": "",
            "accelerator": "",
            "run_ts": run_ts,
        }

    exp_name = "_".join(parts[1:acc_start])
    accelerator = "_".join(parts[acc_start:])
    # legacy path can still have .bit hanging around depending on split.
    if accelerator.endswith(".bit"):
        accelerator = accelerator[: -len(".bit")]

    exp_version = ""
    ver_match = re.search(r"_(\d+_\d+)$", accelerator)
    if ver_match:
        exp_version = ver_match.group(1)

    return {
        "board": board,
        "exp_name": exp_name,
        "exp_version": exp_version,
        "accelerator": accelerator,
        "run_ts": run_ts,
    }

HWC_RE = re.compile(
    r"HWC\[(?P<idx>\d+)\]\s*\|\s*Current State:\s*\d+\s*\|\s*Cycle Count:\s*(?P<count>\d+)"
)
FPGA_TOTAL_RE = re.compile(r"^fpga_total:\s*(?P<value>\d+)")
DRIVER_RE = re.compile(r"^driver:\s*(?P<value>\d+)")

# DMA sections look like: "-----------DMA: 0-----------"
DMA_ID_RE = re.compile(r"^[-=]*\s*DMA:\s*(?P<id>\d+)\s*[-=]*$")

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


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except Exception:
        return False


def parse_log(path: Path) -> Dict[str, str]:
    metrics: Dict[str, str] = {"log_file": path.name}
    if not path.exists():
        return metrics

    # Track which DMA block we're currently inside so metrics get namespaced per DMA id
    current_dma: str | None = None

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

        dma_id = DMA_ID_RE.match(line)
        if dma_id:
            current_dma = dma_id.group("id")
            continue

        dma = DMA_RE.match(line)
        if dma:
            if dma.group("recv"):
                if current_dma is not None:
                    metrics[f"dma{current_dma}_transferred_recv_bytes"] = dma.group("value")
                else:
                    metrics["dma_transferred_recv_bytes"] = dma.group("value")
            else:
                if current_dma is not None:
                    metrics[f"dma{current_dma}_transferred_bytes"] = dma.group("value")
                else:
                    metrics["dma_transferred_bytes"] = dma.group("value")
            continue

        for regex, key in [
            (SEND_WAIT_RE, "send_wait"),
            (RECV_WAIT_RE, "recv_wait"),
            (SEND_SPEED_RE, "send_speed_mb_s"),
            (RECV_SPEED_RE, "recv_speed_mb_s"),
            (SEND_COUNT_RE, "send_count"),
            (RECV_COUNT_RE, "recv_count"),
            (SEND_PER_RE, "data_per_send_bytes"),
            (RECV_PER_RE, "data_per_recv_bytes"),
        ]:
            match = regex.match(line)
            if match:
                if current_dma is not None:
                    metrics[f"dma{current_dma}_{key}"] = match.group("value")
                else:
                    metrics[f"dma_{key}"] = match.group("value")
                break

    return metrics


def find_log_files(dataset_dir: Path) -> List[Path]:
    """Find all log files under a dataset/results folder.

    Supported layouts:
    - <dataset>/results/**/*.log
    - <dataset>/runs/*/results/*.log (legacy)
    - Any direct *.log in the dataset directory
    """
    candidates: List[Path] = []

    if (dataset_dir / "results").exists():
        candidates.extend(sorted((dataset_dir / "results").rglob("*.log")))

    runs_dir = dataset_dir / "runs"
    if runs_dir.exists():
        candidates.extend(sorted(runs_dir.glob("*/results/*.log")))

    candidates.extend(sorted(dataset_dir.glob("*.log")))

    # De-dupe while preserving order
    seen: Set[Path] = set()
    out: List[Path] = []
    for p in candidates:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return out


def collect_log_metrics(log_path: Path) -> Dict[str, str]:
    metrics = parse_log(log_path)
    metrics.update(parse_filename(log_path.name))
    # Keep the log filename too
    metrics.setdefault("log_file", log_path.name)
    return metrics


def all_metric_keys(rows: Iterable[Dict[str, str]]) -> list[str]:
    keys = {"log_file", "board", "exp_name", "exp_version", "accelerator", "run_ts"}
    for row in rows:
        keys.update(row.keys())

    # Front-load the identifiers for easy filtering/sorting in spreadsheets.
    ordered_prefix = ["board", "exp_name", "exp_version", "accelerator", "run_ts", "log_file"]
    ordered = ordered_prefix + sorted(k for k in keys if k not in set(ordered_prefix))
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

    log_files = find_log_files(dataset_dir)
    if not log_files:
        raise SystemExit(f"No .log files found under {dataset_dir}")

    rows = [collect_log_metrics(p) for p in log_files]

    # Stable row ordering for diffs/plotting.
    def sort_key(r: Dict[str, str]) -> tuple:
        return (
            r.get("board", ""),
            r.get("exp_name", ""),
            r.get("exp_version", ""),
            r.get("run_ts", ""),
            r.get("log_file", ""),
        )

    rows.sort(key=sort_key)

    # Normalize HWC columns across logs: if some logs do not have certain hwc_*_cycles, fill with 0.
    fieldnames = all_metric_keys(rows)
    hwc_cols = [c for c in fieldnames if c.startswith("hwc_") and c.endswith("_cycles")]
    for row in rows:
        for c in hwc_cols:
            row.setdefault(c, "0")
        # also default missing validation/driver/fpga_total to 0/empty for stable CSV
        row.setdefault("fpga_total", "0")
        row.setdefault("driver", "0")
        row.setdefault("validation", "")

        # Fill any other missing/empty numeric metrics with 0.
        # Keep identifier columns as-is.
        for key in fieldnames:
            if key in {"board", "exp_name", "exp_version", "accelerator", "run_ts", "log_file", "validation"}:
                continue
            val = row.get(key)
            if val is None or val == "":
                row[key] = "0"
            elif isinstance(val, str) and _is_number(val) is False:
                # Non-numeric noise shouldn't happen for metric fields; normalize to 0.
                row[key] = "0"

    output_path = args.output or (dataset_dir / "performance_summary.csv")

    with output_path.open("w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
