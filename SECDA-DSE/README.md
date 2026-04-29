# SECDA-DSE

Utilities for taking LLM-generated experiments from `input_space/` and preparing them for the `DSE_Explorer` pipeline.

## Script

- `llm_to_dse.py`

This script automates:

1. Validate an input experiment folder shape.
2. Place it under `SECDA-DSE/llm_experiments/<experiment-name>/<version>`.
3. Normalize `hw_params.json` board/path metadata.
4. Rewrite stale `//experiments/...` references to `//SECDA-DSE/llm_experiments/...`.
5. Run `dse_run.py` with a flow preset (default: `sim_lite`).

## Required Input Layout

The input experiment folder must include:

- `BUILD`
- `experiment.cc`
- `hw_params.json`
- `accelerator/`

## Example

```bash
python3 SECDA-DSE/llm_to_dse.py \
  -i SECDA-DSE/input_space/v1 \
  -e Acc-2 \
  -V v1 \
  -b KRIA \
  -s 2 \
  -F
```

Default output root is:

- `SECDA-DSE/llm_generated`

So the DSE results land under a folder like:

- `SECDA-DSE/llm_generated/Acc-2_v1`

## Useful Options

- `-i`, `--input`: source LLM experiment folder.
- `-e`, `--experiment-name`: target experiment name under `llm_experiments`.
- `-V`, `--version`: target experiment version (for example `v1`).
- `-b`, `--target-board`: board normalization target (`KRIA` or `Z1`).
- `-o`, `--output-root`: output root for generated DSE runs/results.
- `-c`, `--settings`: path to `dse_setting.json`.
- `-s`, `--sample`: deterministic sample size for generate stage.
- `-f`, `--flow`: dse_run flow preset (default: `sim_lite`).
- `-k`, `--keep-input` / `--no-keep-input`: copy (default) or move source experiment.
- `-F`, `--force`: overwrite existing destination experiment.
- `-n`, `--no-run`: only validate/normalize/place; do not run DSE.
- `-d`, `--dry-run`: forward dry-run mode to `dse_run.py`.
- `-x`, `--strict-sim`: fail immediately if dse_run fails when using a sim-containing flow.
- `-m`, `--monitor`: forward to `dse_run.py --monitor`.
- `-I`, `--monitor-interval N`: forward to `dse_run.py --monitor-interval`.
- `-r`, `--resume`: forward to `dse_run.py --resume`.
- `-P`, `--project-status-filename NAME`: forward to `dse_run.py`.
- `-R`, `--run-status-filename NAME`: forward to `dse_run.py`.
- `-T`, `--enable-timeouts` / `--no-enable-timeouts`: forward timeout toggle to `dse_run.py`.
- `-B`, `--timeout-sim-bin`, `-U`, `--timeout-sim-run`, `-H`, `--timeout-hls`, `-X`, `--timeout-hlx`, `-C`, `--timeout-fpga-compile`, `-E`, `--timeout-fpga-exec`: forward timeout values to `dse_run.py`.
- `-a`, `--dse-run-arg ARG`: append additional raw argument to `dse_run.py` (repeatable).

## Notes

- The default flow is `sim_lite`.
- By default, failures are reported as warnings and execution continues unless `--strict-sim` is set with a sim-containing flow.
- You can use `--flow fpga` or `--flow all` for HLX/FPGA-inclusive runs.
- SECDA-DSE now forwards common dse_run controls directly, including monitor and timeout options.
