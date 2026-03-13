# DSE_Explorer

Small tool to generate experiment copies and per-run bash scripts from an experiment's `hw_params.json`.

## Features
- Supports new `hw_params.json` format with `groups` and `parameters`.
- Groups are paired index-wise; parameters listed together in a group are NOT permuted against each other.
- Ungrouped parameters are cross-producted as usual.
- Produces a `runs.csv` with run_id, run_name, params, and source_experiment.
- Optional `--dry-run` mode prints planned actions without copying files.
- Optional `--sample N` creates a deterministic sample of N runs.
- Generated `hw_gen_*.sh` scripts automate hardware generation (HLS/HLX) using `hardware_automation/hw_gen.py`.
- Generated `run_*.sh` scripts handle compile/upload/run on the target device.
- Per-run manifests are written to `hardware_gen/manifest.json`.

### Quick example

```bash
python3 DSE_Explorer/dse_explorer.py \
  --experiment experiments/mm_exp/v1 \
  --dry-run
```

## Settings

`DSE_Explorer/dse_setting.json` controls paths and filenames (output root,
results folder name, hw params filename, hardware generation script path,
global script names, and HLX report filenames). Use `--settings` to point to a
custom file.

## Hardware generation run scripts

Each generated `hw_gen_*.sh` script will:
- Create `hardware_gen/` and write `hardware_gen/hw_config.json` from `hw_params.json`.
- Run `hardware_automation/hw_gen.py` to generate Vivado HLS/HLX projects.
- Execute HLS/HLX via the generated `hardware_gen/<acc_tag>/run.sh`.
- Focus only on hardware generation; run `run_*.sh` separately for remote execution.


## Generated Run Folder

### HW Gen
Each experiment output folder also includes `hw_gen_all.sh`, which runs every
`hw_gen_*.sh` in that experiment sequentially. Any arguments passed to
`hw_gen_all.sh` are forwarded to each `hw_gen_*.sh`.


### Run Exp
Each experiment output folder also includes `run_all.sh`, which runs every
`run_*.sh` in that experiment sequentially. Any arguments passed to
`run_all.sh` are forwarded to each `run_*.sh`.


### Collect Results
Each experiment output folder also includes `collect_all_results.sh`, which
copies each run's `results/` folder into `all_results/<experiment>/<run_id>/`.
It replaces any previously collected folder for the same run ID.

Each generated `run_*.sh` also copies the remote run log back to the host
output folder after completion, storing logs in `results/`.

HLX utilization/timing reports generated during `hw_gen_*.sh` runs are copied
into the same per-run `results/` folder, along with `outputHLS.log` and
`outputHLX.log` when present.


### Collect Dataset
Each experiment output folder also includes `collect_dataset.sh`, which builds
a dataset under `dataset/`:
- Per-run accelerator sources are copied to `dataset/runs/<run_id>/sources/`.
- Per-run execution/hardware results are copied to `dataset/runs/<run_id>/results/` when present.
- `runs.csv` is copied to `dataset/` when available.


## HW Gen/Exp Run Flags
Runtime flags (can be env vars or script args):
- `RUN_HLS` (default 1)
- `RUN_HLX` (default 1)
- `OFFLOAD_HLS_HLX` (default 0)
- `COPY_BITS` (default 1)
- `FORCE_HW_GEN` (default 0)
- `RUN_REMOTE` (default 1)
- `DRY_RUN` (default 0)

## Notes
- The script will search for `hw_params.json` inside the experiment folder if not explicitly provided.
- By default, generated experiments are placed under `DSE_Explorer/generated/<experiment>/`,
  where `<experiment>` is `<exp_name>_<exp_version>` (for example, `mm_exp_v1`).
- Vivado/HLS/HLX paths are read from `config.json` via `hardware_automation/hw_gen.py`.
- Remote compilation/upload/run uses board details and data/model paths from `config.json`.
