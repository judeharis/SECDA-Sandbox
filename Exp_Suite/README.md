SECDA exp_gen_suite

This folder contains the experiment generation helper script and utilities used to build and run experiment binaries on target hardware.

Files
- `secda_exp_gen.sh` - Main entrypoint (refactored). Function-based, supports logging, CLI flags, dry-run and board selection from `config.json`.
- `configs.sh` - Project-specific array of hardware experiment identifiers (sourced by the script).
- `scripts/load_bitstream.py` - Helper to load bitstreams on remote boards.

Prerequisites
- bash (POSIX shell)
- jq (to read JSON config)
- rsync, ssh
- bazel6 (used for building experiments)
- python3 on the target board (for `load_bitstream.py`)

Notes
- The script reads board definitions from the project's `config.json` (top-level) under the `boards` object.
- The script is resilient to being run from the repository root; it resolves paths relative to the script location.

Common commands
- Show help:

```bash
./exp_gen_suite/secda_exp_gen.sh --help
```

- List available boards (reads `config.json`):

```bash
./exp_gen_suite/secda_exp_gen.sh --config config.json --list-boards
```

- Dry-run example (safe, prints the remote/rsync/bazel commands without executing):

```bash
./exp_gen_suite/secda_exp_gen.sh --config config.json --init --build --test --verbose --dry-run
```

- Real run (be careful — this will execute remote commands):

```bash
./exp_gen_suite/secda_exp_gen.sh --config config.json --init --build --test --verbose
```

Troubleshooting and tips
- If the script complains that it can't find `configs.sh`, make sure `configs.sh` is present in this folder; it should define `hw_array` used by the script.
- If `shellcheck` is desired for linting, install it (on Debian/Ubuntu):

```bash
sudo apt update && sudo apt install shellcheck
```

or via snap:

```bash
sudo snap install shellcheck
```

- Ensure `jq`, `rsync`, and `ssh` are installed on the host. `python3` should be available on the remote board.

Security
- The script executes remote commands via SSH as configured in `config.json`. Ensure your SSH keys and access controls are correct.

License / Attribution
- This file documents the local `exp_gen_suite` utilities and how to use the refactored `secda_exp_gen.sh` script.
