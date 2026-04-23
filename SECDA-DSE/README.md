# SECDA-DSE

Utilities for taking LLM-generated experiments from `input_space/` and preparing them for the `DSE_Explorer` pipeline.

## Script

- `llm_to_dse.py`

This script automates:

1. Validate an input experiment folder shape.
2. Place it under `SECDA-DSE/llm_experiments/<experiment-name>/<version>`.
3. Normalize `hw_params.json` board/path metadata.
4. Rewrite stale `//experiments/...` references to `//SECDA-DSE/llm_experiments/...`.
5. Run `dse_run.py` stages: `generate -> hls -> sim -> collect`.

## Required Input Layout

The input experiment folder must include:

- `BUILD`
- `experiment.cc`
- `hw_params.json`
- `accelerator/`

## Example

```bash
python3 SECDA-DSE/llm_to_dse.py \
  --input SECDA-DSE/input_space/v1 \
  --experiment-name Acc-2 \
  --version v1 \
  --target-board KRIA \
  --keep-input \
  --force
```

Default output root is:

- `SECDA-DSE/llm_generated`

So the DSE results land under a folder like:

- `SECDA-DSE/llm_generated/Acc-2_v1`

## Useful Options

- `--keep-input`: copy source instead of moving it.
- `--force`: overwrite existing destination experiment.
- `--sample N`: deterministic sample size for generate stage.
- `--no-run`: only validate/normalize/place; do not run DSE.
- `--dry-run`: forward dry-run mode to `dse_run.py` stages.
- `--strict-sim`: fail immediately if simulation stage fails.

## Notes

- The script runs HLS and simulation by default (not HLX).
- By default, simulation failures are reported as warnings and the flow continues to collect available outputs.
- If you later run HLX via `dse_run.py --stage hlx`, HLS status precondition must be satisfied.
