#!/usr/bin/env bash
set -euo pipefail

RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$RUN_DIR"

./hw_gen_all.sh
./run_all.sh
./collect_all_results.sh
./collect_dataset.sh
