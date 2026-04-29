# dse_run.py README

`dse_run.py` is a unified Design Space Exploration (DSE) orchestrator for this repository.

It wraps the existing `DSE_Explorer` workflow and provides explicit pipeline stages:

- `generate`: create run folders and scripts from an experiment definition
- `hls`: run only HLS for each run
- `hlx`: run only HLX for each run
- `hw`: run both HLS and HLX for each run
- `run`: build/upload/execute each run on target board
- `sim`: build and execute simulation per run
- `collect`: collect outputs and assemble `dataset/`
- `parse`: parse hardware + performance summaries into CSV files
- `all`: run all stages in order

It supports `--resume` to skip run folders that are already complete according to `Run_Status.json`.

Upgrade Plan 2 status tracking files:

- Project-level: `Project_Status.json` in the generated experiment root
- Per-run: `Run_Status.json` in each run folder

`Project_Status.json` is intentionally compact. It stores project-level metadata in `project`, per-run execution detail in `runs`, and aggregate counters in `summary`.

`Run_Status.json` is also compact on disk: stages that never started are omitted, and stage records keep only populated fields so the file is easier to scan.

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

- `--stage <generate|hls|hlx|hw|run|sim|collect|parse|all>`
  - Stage to execute
  - Default: `all`

- `--flow <sim|fpga|lite|sim_lite|all>`
  - Executes a predefined stage flow and overrides `--stage` selection
  - Default: `sim_lite`
  - Flow mappings:
    - `sim`: Variant Generation -> Simulation Binary Generation -> SystemC Simulation -> Results Collection
    - `fpga`: Variant Generation -> HLS -> HLX -> FPGA Binary Compilation -> FPGA Mapping + Experiment Execution -> Results Collection -> Parse
    - `lite`: Variant Generation -> HLS -> Results Collection
    - `sim_lite`: Variant Generation -> Simulation Binary Generation -> SystemC Simulation -> HLS -> Results Collection
    - `all`: Variant Generation -> Simulation Binary Generation -> SystemC Simulation -> HLS -> HLX -> FPGA Binary Compilation -> FPGA Mapping + Experiment Execution -> Results Collection -> Parse

- `--resume`
  - Skip run directories already successful:
    - HLS stage skip condition: `Run_Status.json` stage `hls` is `success`
    - HLX stage skip condition: `Run_Status.json` stage `hlx` is `success`
    - SIM stage skip condition: simulation stages are already `success`
    - Run stage skip condition: stage 6 and stage 7 are `success`

- `--dry-run`
  - Forwards dry-run mode to generated scripts when supported

- `--project-status-filename <name>`
  - Project-level status filename
  - Default: `Project_Status.json`

- `--run-status-filename <name>`
  - Per-run status filename
  - Default: `Run_Status.json`

- `--monitor`
  - Launches a live Project_Status monitor in a second terminal while `dse_run.py` continues in the current terminal

- `--monitor-interval <seconds>`
  - Refresh interval for the live monitor
  - Default: `2.0`

- `--enable-timeouts` / `--no-enable-timeouts`
  - Enable/disable timeout enforcement for workflow stages
  - Default: enabled

- Timeout controls (seconds):
  - `--timeout-sim-bin` (default: `900`)
  - `--timeout-sim-run` (default: `1800`)
  - `--timeout-hls` (default: `3600`)
  - `--timeout-hlx` (default: `10800`)
  - `--timeout-fpga-compile` (default: `1800`)
  - `--timeout-fpga-exec` (default: `1800`)

## Stage Behavior

### 1) generate

Runs `DSE_Explorer/dse_explorer.py` to generate run directories and stage scripts.

### 2) hls

Runs only HLS for generated runs (per-run execution).

- Executes per-run `hw_gen_*.sh 1 0`.
- On failure/timeout, HLX is marked skipped for that run.

### 3) hlx

Runs only HLX for generated runs.

- Executes per-run `hw_gen_*.sh 0 1`.
- Runs missing HLS success are marked skipped for HLX.

### 4) hw

Runs hardware generation scripts (HLS then HLX) per run:

- HLS call: `hw_gen_*.sh 1 0`
- HLX call: `hw_gen_*.sh 0 1`

### 5) run

Runs FPGA build/execute scripts per run and updates both stage 6 and stage 7 status:

- Executes per-run `run_*.sh`.
- If HLX is not successful, stage 6 and stage 7 are marked skipped.

### 6) sim

Runs simulation script per run and updates both stage 2 and stage 3 status:

- Executes per-run `sim_*.sh`.
- If simulation binary generation fails, SystemC simulation is marked skipped.

### 6) collect

Runs both:

- `collect_all_results.sh`
- `collect_dataset.sh`

### 7) parse

Reads `dataset/` and runs:

- `DSE_Explorer/scripts/parse_hardware.py`
- `DSE_Explorer/scripts/parse_performance.py`
- `DSE_Explorer/scripts/parse_hls.py`

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
- `dataset/hls_summary.csv` (HLS csynth summary)

## Examples

### Full pipeline

```bash
python3 dse_run.py --experiment experiments/mm_exp/v1 --stage all
```

### Full pipeline using flow preset

```bash
python3 dse_run.py --experiment experiments/mm_exp/v1 --flow all
```

### FPGA-focused flow

```bash
python3 dse_run.py --experiment experiments/mm_exp/v1 --flow fpga
```

### Simulation-only flow

```bash
python3 dse_run.py --experiment experiments/mm_exp/v1 --flow sim
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

### Run with live status monitor (two terminals)

```bash
python3 dse_run.py --experiment experiments/mm_exp/v1 --stage all --monitor
```

This keeps normal workflow logs in the current terminal and opens a second terminal that follows `Project_Status.json` in real time.

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

## Status Files and Summary

The script writes:

- `<out_root>/Project_Status.json`
- `<out_root>/<run_id>/Run_Status.json`

`Project_Status.json` top-level `project` fields include:

- `variant_generation`
- `last_updated`
- `samples`
- `generation_command`
- `project_result_status`
- `project_dataset_status`

Run-level stage states follow: `not_started`, `running`, `success`, `failed`, `timeout`, `skipped`.

When available, both `Project_Status.json` and `Run_Status.json` store explicit `log_path` values for the command or artifact log associated with each stage.

`Project_Status.json` includes run counters:

- `total_runs`
- `succeeded_runs`
- `failed_runs`
- `timed_out_runs`
- `skipped_runs`
- `collected_runs`

## Status Summary Printed by Script

After execution, the script prints key output paths including `Project_Status.json`.

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
- `Run_Status.json` is the single per-run status source; legacy generated-script `status.json` is ignored and cleaned when runs are normalized.
- For existing projects, `Project_Status.json` is reset to default at startup of each `dse_run.py` invocation.
