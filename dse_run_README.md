# dse_run.py README

`dse_run.py` is a unified Design Space Exploration (DSE) orchestrator for this repository.

It wraps the existing `DSE_Explorer` workflow and provides explicit pipeline stages:

- `generate`: create run folders and scripts from an experiment definition
- `hls`: run only HLS for each run
- `hlx`: run only HLX for each run
- `hw`: run both HLS and HLX for each run
- `run`: build/upload/execute each run on target board
- `collect`: collect outputs and assemble `dataset/`
- `parse`: parse hardware + performance summaries into CSV files
- `all`: run all stages in order

It also supports `--resume` to skip run folders that are already complete according to `status.json`.

## Location

- Script: `dse_run.py`
- Default settings file: `DSE_Explorer/dse_setting.json`

## Requirements

- Python 3.9+
- A valid experiment folder containing `hw_params.json`
- Toolchain/env required by generated scripts (Bazel, HLS/HLX tools, remote board access, etc.)

## CLI Reference

```bash
python3 dse_run.py --experiment <path> [options]
```

### Required Argument

- `--experiment`
  - Experiment path (relative to repo root or absolute)
  - Example: `experiments/mm_exp/v1`

### Optional Arguments

- `--settings <path>`
  - Settings JSON path
  - Default: `DSE_Explorer/dse_setting.json`

- `--output <path>`
  - Base output directory
  - Default: value of `output_root` in settings (`DSE_Explorer/generated`)

- `--sample <N>`
  - Deterministic sample size for generation stage
  - Default: `0` (no sampling)

- `--stage <generate|hls|hlx|hw|run|collect|parse|all>`
  - Stage to execute
  - Default: `all`

- `--resume`
  - Skip run directories already successful:
    - HLS stage skip condition: `status.json` has successful `hls`
    - HLX stage skip condition: `status.json` has successful `hlx`
    - HW stage skip condition: `status.json` has successful `hls` and `hlx`
    - Run stage skip condition: `status.json` has successful `bazel_build` and `remote_run`

- `--dry-run`
  - Forwards dry-run mode to generated scripts when supported

## Stage Behavior

### 1) generate

Runs `DSE_Explorer/dse_explorer.py` to generate run directories and stage scripts.

### 2) hls

Runs only HLS for generated runs.

- Non-resume mode: runs `hw_gen_all.sh` with `RUN_HLS=1 RUN_HLX=0`
- Resume mode: runs per-run `hw_gen_*.sh 1 0` only when HLS is not marked successful

### 3) hlx

Runs only HLX for generated runs.

Prerequisite:

- HLS must already be completed for all discovered runs (based on `status.json`).
- If any run is missing HLS success, `dse_run.py` halts and prints guidance to run HLS first.

- Non-resume mode: runs `hw_gen_all.sh` with `RUN_HLS=0 RUN_HLX=1`
- Resume mode: runs per-run `hw_gen_*.sh 0 1` only when HLX is not marked successful

### 4) hw

Runs hardware generation scripts:

- Non-resume mode: executes `hw_gen_all.sh` with `RUN_HLS=1 RUN_HLX=1`
- Resume mode: executes per-run `hw_gen_*.sh 1 1` only for unfinished runs

### 5) run

Runs execution scripts:

- Non-resume mode: executes `run_all.sh` in the experiment output root
- Resume mode: executes per-run `run_*.sh` only for unfinished runs

### 6) collect

Runs both:

- `collect_all_results.sh`
- `collect_dataset.sh`

### 7) parse

Reads `dataset/` and runs:

- `DSE_Explorer/parse_hardware.py`
- `DSE_Explorer/parse_performance.py`

Generates summary CSVs in the dataset directory.

## Output Layout

The output root is:

```text
<output_base>/<experiment_folder_format>
```

By default, `experiment_folder_format` is `{exp_name}_{exp_version}`.

Example:

- Experiment: `experiments/mm_exp/v1`
- Output base: `DSE_Explorer/generated`
- Output root: `DSE_Explorer/generated/mm_exp_v1`

Important generated paths:

- Per-run folders containing `hw_params.json`
- `dataset/`
- `dataset/results_summary.csv` (hardware summary)
- `dataset/performance_summary.csv` (performance summary)

## Examples

### Full pipeline

```bash
python3 dse_run.py --experiment experiments/mm_exp/v1 --stage all
```

### Generate only (with deterministic sample)

```bash
python3 dse_run.py --experiment experiments/mm_exp/v1 --stage generate --sample 16
```

### Resume incomplete HW only

```bash
python3 dse_run.py --experiment experiments/mm_exp/v1 --stage hw --resume
```

### HLS-only stage

```bash
python3 dse_run.py --experiment experiments/mm_exp/v1 --stage hls
```

### HLX-only stage

```bash
python3 dse_run.py --experiment experiments/mm_exp/v1 --stage hlx
```

### Resume incomplete run stage only

```bash
python3 dse_run.py --experiment experiments/mm_exp/v1 --stage run --resume
```

### Collect + parse after previous execution

```bash
python3 dse_run.py --experiment experiments/mm_exp/v1 --stage collect
python3 dse_run.py --experiment experiments/mm_exp/v1 --stage parse
```

### Use custom output and settings

```bash
python3 dse_run.py \
  --experiment experiments/mm_exp/v1 \
  --settings /path/to/dse_setting.json \
  --output /path/to/generated \
  --stage all
```

## Status Summary Printed by Script

After execution, the script prints:

- Total run folders discovered
- How many have HW completed (`hls` + `hlx`)
- How many have run completed (`bazel_build` + `remote_run`)
- Key output file locations

## Common Errors

- `Experiment path not found`
  - Verify `--experiment` path and repo location.

- `Output root does not exist ... Run generate stage first.`
  - Run with `--stage generate` (or `--stage all`) before `hw/run/collect/parse`.

- `Dataset directory not found ... Run collect stage first.`
  - Run `--stage collect` before `--stage parse`.

## Notes

- `--dry-run` behavior depends on generated scripts honoring `DRY_RUN=1`.
- The script uses your current Python interpreter (`sys.executable`) for child Python scripts.
