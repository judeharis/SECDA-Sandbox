from pathlib import Path
import json


def _write_lines(sh, lines: list[str]) -> None:
    for line in lines:
        sh.write(line)


def _write_update_status_function(sh, status_filename: str):
    """Write a bash helper ``update_status`` into *sh*."""
    sh.write(f'STATUS_FILE="$RUN_DIR/{status_filename}"\n')
    sh.write('update_status() {\n')
    sh.write('  local stage="$1" action="$2" extra="${3:-}"\n')
    sh.write('  STATUS_FILE="$STATUS_FILE" STAGE="$stage" ACTION="$action" EXTRA="$extra" python3 - <<\'_PYSTAT\'\n')
    sh.write('import json, os, datetime, sys\n')
    sh.write('from pathlib import Path\n')
    sh.write('sf = Path(os.environ["STATUS_FILE"])\n')
    sh.write('stage = os.environ["STAGE"]\n')
    sh.write('action = os.environ["ACTION"]\n')
    sh.write('extra = os.environ.get("EXTRA", "")\n')
    sh.write('now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")\n')
    sh.write('blank = {"attempted": False, "success": None, "exit_code": None, "timestamp": None, "duration_seconds": None}\n')
    sh.write('try:\n')
    sh.write('    data = json.loads(sf.read_text()) if sf.exists() else {}\n')
    sh.write('except Exception:\n')
    sh.write('    data = {}\n')
    sh.write('s = data.setdefault(stage, dict(blank))\n')
    sh.write('for k, v in blank.items():\n')
    sh.write('    s.setdefault(k, v)\n')
    sh.write('if action == "attempted":\n')
    sh.write('    s["attempted"] = True\n')
    sh.write('    s["timestamp"] = now\n')
    sh.write('elif action == "success":\n')
    sh.write('    s["success"] = True\n')
    sh.write('    if extra: s["exit_code"] = int(extra)\n')
    sh.write('    if not s["timestamp"]: s["timestamp"] = now\n')
    sh.write('elif action == "failure":\n')
    sh.write('    s["success"] = False\n')
    sh.write('    if extra: s["exit_code"] = int(extra)\n')
    sh.write('    if not s["timestamp"]: s["timestamp"] = now\n')
    sh.write('elif action == "duration":\n')
    sh.write('    try: s["duration_seconds"] = float(extra)\n')
    sh.write('    except ValueError: pass\n')
    sh.write('sf.parent.mkdir(parents=True, exist_ok=True)\n')
    sh.write('sf.write_text(json.dumps(data, indent=2) + "\\n")\n')
    sh.write('_PYSTAT\n')
    sh.write('}\n\n')


def _write_parse_hls_hlx_status(sh, hw_gen_dir_var: str, acc_tag_var: str):
    """Write bash snippet that parses HLS/HLX logs and updates status."""
    sh.write(f'HLS_LOG="${{{hw_gen_dir_var}}}/${{{acc_tag_var}}}/outputHLS.log"\n')
    sh.write(f'HLX_LOG="${{{hw_gen_dir_var}}}/${{{acc_tag_var}}}/outputHLX.log"\n')
    sh.write('HLS_LOG="$HLS_LOG" HLX_LOG="$HLX_LOG" STATUS_FILE="$STATUS_FILE" python3 - <<\'_PYHLSTAT\'\n')
    sh.write('import json, os, re, datetime\n')
    sh.write('from pathlib import Path\n')
    sh.write('sf = Path(os.environ["STATUS_FILE"])\n')
    sh.write('hls_log = Path(os.environ["HLS_LOG"])\n')
    sh.write('hlx_log = Path(os.environ["HLX_LOG"])\n')
    sh.write('now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")\n')
    sh.write('blank = {"attempted": False, "success": None, "exit_code": None, "timestamp": None, "duration_seconds": None}\n')
    sh.write('try:\n')
    sh.write('    data = json.loads(sf.read_text()) if sf.exists() else {}\n')
    sh.write('except Exception:\n')
    sh.write('    data = {}\n')
    sh.write('elapsed_re = re.compile(r"(?:Elapsed|Total elapsed)\\s+time[:\\s]+(\\d+)\\s*(?:hours?|h)[,\\s]+(\\d+)\\s*(?:minutes?|min|m)[,\\s]+(\\d+)\\s*(?:seconds?|sec|s)", re.I)\n')
    sh.write('def parse_log(log_path, stage_key):\n')
    sh.write('    s = data.setdefault(stage_key, dict(blank))\n')
    sh.write('    for k, v in blank.items():\n')
    sh.write('        s.setdefault(k, v)\n')
    sh.write('    if not log_path.exists():\n')
    sh.write('        return\n')
    sh.write('    text = log_path.read_text(errors="replace")\n')
    sh.write('    s["attempted"] = True\n')
    sh.write('    if not s["timestamp"]:\n')
    sh.write('        s["timestamp"] = now\n')
    sh.write('    exit_tag = f"{stage_key.upper()} exit status: 1"\n')
    sh.write('    if exit_tag in text:\n')
    sh.write('        s["success"] = False\n')
    sh.write('    elif re.search(r"Finished\\s+(?:Generating|C synthesis)", text, re.I) or re.search(r"write_bitstream completed successfully", text, re.I):\n')
    sh.write('        s["success"] = True\n')
    sh.write('    matches = list(elapsed_re.finditer(text))\n')
    sh.write('    if matches:\n')
    sh.write('        m = matches[-1]\n')
    sh.write('        s["duration_seconds"] = int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3))\n')
    sh.write('parse_log(hls_log, "hls")\n')
    sh.write('parse_log(hlx_log, "hlx")\n')
    sh.write('sf.parent.mkdir(parents=True, exist_ok=True)\n')
    sh.write('sf.write_text(json.dumps(data, indent=2) + "\\n")\n')
    sh.write('_PYHLSTAT\n')


def _write_board_info_section(sh, settings: dict) -> None:
    _write_lines(sh, [
        'BOARD_INFO=$(REPO_ROOT="$REPO_ROOT" python3 - <<\'PY\'\n',
        'import json, os, shlex\n',
        'from pathlib import Path\n',
        'repo_root = Path(os.environ.get("REPO_ROOT", ".")).resolve()\n',
        'run_dir = Path(os.environ.get("RUN_DIR", ".")).resolve()\n',
        f'config_path = repo_root / "{settings["config_json"]}"\n',
        f'data = json.load(open("{settings["hw_params_filename"]}"))\n',
        'hg = data.get("hardware_gen") or {}\n',
        'cfg = json.load(config_path.open())\n',
        'boards = cfg.get("boards", {})\n',
        'board_name = hg.get("board") or (list(boards.keys())[0] if boards else "")\n',
        'b = boards.get(board_name, {})\n',
        f'suffix_fmt = {json.dumps(settings["artifact_suffix_format"])}\n',
        'run_id = os.environ.get("RUN_ID", "")\n',
        'suffix = suffix_fmt.format(run_id=run_id) if run_id else ""\n',
        'exp_name = hg.get("del")\n',
        'exp_version = f"v{hg.get(\'del_version\')}" if hg.get("del_version") is not None else ""\n',
        'acc_name = hg.get("acc_name")\n',
        'if run_id and acc_name:\n',
        '    acc_name = f"{acc_name}_{run_id}"\n',
        'acc_version = hg.get("acc_version")\n',
        'acc_sub = hg.get("acc_sub_version")\n',
        'acc_bit = f"{acc_name}_{acc_version}_{acc_sub}{suffix}.bit"\n',
        'acc_hwh = f"{acc_name}_{acc_version}_{acc_sub}{suffix}.hwh"\n',
        'bin_name = f"{exp_name}_{exp_version}{suffix}"\n',
        'try:\n',
        '    rel_path = run_dir.relative_to(repo_root)\n',
        '    path_to_exp = rel_path.as_posix()\n',
        'except ValueError:\n',
        '    path_to_exp = f"experiments/{exp_name}/{exp_version}"\n',
        'bazel_target = f"//{path_to_exp}:exp"\n',
        'bin_path = f"{repo_root}/bazel-bin/{path_to_exp}/exp"\n',
        'bitstream_dir = cfg.get("bitstream_dir") or str(repo_root / "hardware_automation" / "bitstreams")\n',
        'bit_src = f"{bitstream_dir}/{board_name}/{acc_bit}"\n',
        'hwh_src = f"{bitstream_dir}/{board_name}/{acc_hwh}"\n',
        'data_dir = cfg.get("data_dir", "")\n',
        'models_dirs = cfg.get("models_dirs", [])\n',
        'lines = {\n',
        '  "BOARD_NAME": board_name,\n',
        '  "BOARD_USER": b.get("board_user", ""),\n',
        '  "BOARD_HOSTNAME": b.get("board_hostname", ""),\n',
        '  "BOARD_PORT": str(b.get("board_port", "")),\n',
        '  "BOARD_DIR": b.get("board_dir", ""),\n',
        '  "EXP_NAME": exp_name or "",\n',
        '  "EXP_VERSION": exp_version,\n',
        '  "BIN_NAME": bin_name,\n',
        '  "BAZEL_TARGET": bazel_target,\n',
        '  "BIN_PATH": bin_path,\n',
        '  "ACC_BIT": acc_bit,\n',
        '  "ACC_HWH": acc_hwh,\n',
        '  "BITSTREAM_SRC": bit_src,\n',
        '  "HWH_SRC": hwh_src,\n',
        '  "DATA_DIR": data_dir,\n',
        '  "MODELS_DIRS": ":".join(models_dirs),\n',
        '}\n',
        'for k, v in lines.items():\n',
        '    print(f"{k}={shlex.quote(str(v))}")\n',
        'PY\n',
        ')\n',
        'eval "$BOARD_INFO"\n',
        'if [ -z "$BOARD_NAME" ] || [ -z "$BOARD_USER" ] || [ -z "$BOARD_HOSTNAME" ] || [ -z "$BOARD_DIR" ]; then\n',
        '  echo "Board configuration missing in config.json"\n',
        '  exit 1\n',
        'fi\n\n',
    ])


def _write_hw_gen_prelude(sh, settings: dict, run_id: str, run_name: str, failure_logs: list[str]) -> None:
    _write_lines(sh, [
        '#!/usr/bin/env bash\n',
        'set -euo pipefail\n\n',
        f'RUN_ID="{run_id}"\n',
        'export RUN_ID\n',
        f'RUN_NAME="{run_name}"\n',
        'RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n',
        f'RESULTS_DIR="$RUN_DIR/{settings["results_dir_name"]}"\n',
        'REPO_ROOT="$RUN_DIR"\n',
        f'while [ ! -f "$REPO_ROOT/{settings["repo_root_marker"]}" ] && [ "$REPO_ROOT" != "/" ]; do\n',
        '  REPO_ROOT="$(dirname "$REPO_ROOT")"\n',
        'done\n',
        f'if [ ! -f "$REPO_ROOT/{settings["repo_root_marker"]}" ]; then\n',
        f'  echo "{settings["repo_root_marker"]} not found; cannot locate repo root"\n',
        '  exit 1\n',
        'fi\n\n',
        'mkdir -p "$RESULTS_DIR"\n\n',
        f'for report in {" ".join(settings["hlx_reports"])}; do rm -f "$RESULTS_DIR/$report"; done\n',
        f'for log in {" ".join(failure_logs)}; do rm -f "$RESULTS_DIR/$log"; done\n\n',
        f'HW_GEN_DIR="$RUN_DIR/{settings["hw_gen_dir_name"]}"\n',
        f'CONFIG_PATH="$HW_GEN_DIR/{settings["hw_config_filename"]}"\n',
        f'MANIFEST_PATH="$HW_GEN_DIR/{settings["manifest_filename"]}"\n',
        'mkdir -p "$HW_GEN_DIR"\n\n',
    ])
    _write_update_status_function(sh, settings["status_filename"])
    suffix = settings["artifact_suffix_format"].format(run_id=run_id)
    _write_lines(sh, [
        'RUN_HLS="${RUN_HLS:-1}"\n',
        'RUN_HLX="${RUN_HLX:-1}"\n',
        'OFFLOAD_HLS_HLX="${OFFLOAD_HLS_HLX:-0}"\n',
        'COPY_BITS="${COPY_BITS:-1}"\n',
        'FORCE_HW_GEN="1"\n',
        'DRY_RUN="${DRY_RUN:-0}"\n',
        f'BITSTREAM_SUFFIX="${{BITSTREAM_SUFFIX:-{suffix}}}"\n',
        'if [ $# -ge 1 ]; then RUN_HLS="$1"; fi\n',
        'if [ $# -ge 2 ]; then RUN_HLX="$2"; fi\n',
        'if [ $# -ge 3 ]; then OFFLOAD_HLS_HLX="$3"; fi\n',
        'if [ $# -ge 4 ]; then COPY_BITS="$4"; fi\n\n',
    ])


def _write_hw_gen_config_section(sh, settings: dict, run_id: str, run_name: str) -> None:
    _write_lines(sh, [
        'ACC_TAG=$(CONFIG_PATH="$CONFIG_PATH" python3 - <<\'PY\'\n',
        'import json, os, sys\n',
        'from pathlib import Path\n',
        'cfg = os.environ.get("CONFIG_PATH", "hardware_gen/hw_config.json")\n',
        'run_dir = Path(".").resolve()\n',
        f'hw_path = Path("{settings["hw_params_filename"]}")\n',
        'if not hw_path.exists():\n',
        '    print("", end="")\n',
        '    sys.exit(2)\n',
        'data = json.load(hw_path.open())\n',
        'hg = data.get("hardware_gen")\n',
        'if not isinstance(hg, dict):\n',
        '    print("", end="")\n',
        '    sys.exit(3)\n',
        'run_id = os.environ.get("RUN_ID", "").strip()\n',
        'acc_name = str(hg.get("acc_name", ""))\n',
        'if run_id:\n',
        '    acc_name = f"{acc_name}_{run_id}" if acc_name else run_id\n',
        'hg["acc_name"] = acc_name\n',
        'acc_src = (run_dir / "accelerator").resolve()\n',
        f'acc_link = (run_dir / "{settings["hw_gen_dir_name"]}" / "acc_link").resolve()\n',
        'hg["acc_src"] = str(acc_src)\n',
        'hg["acc_link_folder"] = str(acc_link)\n',
        'Path(cfg).parent.mkdir(parents=True, exist_ok=True)\n',
        'with open(cfg, "w") as f:\n',
        '    json.dump(hg, f, indent=2)\n',
        'acc_tag = f"{acc_name}_{hg.get(\'acc_version\')}_{hg.get(\'acc_sub_version\')}"\n',
        'print(acc_tag)\n',
        'PY\n',
        ')\n',
        'if [ -z "$ACC_TAG" ] || [ ! -f "$CONFIG_PATH" ]; then\n',
        '  echo "hardware_gen config missing in hw_params.json"\n',
        '  exit 1\n',
        'fi\n\n',
        'ACC_TAG_ENV="$ACC_TAG" python3 - <<\'PY\'\n',
        'import json, os\n',
        'from pathlib import Path\n',
        f'run_id = "{run_id}"\n',
        f'run_name = "{run_name}"\n',
        'acc_tag = os.environ.get("ACC_TAG_ENV", "")\n',
        'manifest = {\n',
        '  "run_id": run_id,\n',
        '  "run_name": run_name,\n',
        f'  "hw_params": str(Path("{settings["hw_params_filename"]}").resolve()),\n',
        f'  "hardware_gen_dir": str(Path("{settings["hw_gen_dir_name"]}").resolve()),\n',
        '  "acc_tag": acc_tag\n',
        '}\n',
        f'Path("{settings["hw_gen_dir_name"]}/{settings["manifest_filename"]}").write_text(json.dumps(manifest, indent=2))\n',
        'PY\n\n',
    ])


def _write_hw_gen_execution_section(sh, settings: dict, failure_logs: list[str]) -> None:
    _write_lines(sh, [
        'echo "Generating hardware project in $HW_GEN_DIR"\n',
        'if [ "$DRY_RUN" -eq 1 ]; then\n',
        f'  echo "DRY-RUN: SECDA_OUT_DIR=$HW_GEN_DIR python3 $REPO_ROOT/{settings["hw_gen_script"]} $CONFIG_PATH"\n',
        'else\n',
        f'  SECDA_OUT_DIR="$HW_GEN_DIR" python3 "$REPO_ROOT/{settings["hw_gen_script"]}" "$CONFIG_PATH"\n',
        'fi\n\n',
        'RUN_HW="$HW_GEN_DIR/$ACC_TAG/run.sh"\n',
        'if [ ! -x "$RUN_HW" ]; then\n',
        '  echo "Hardware run script not found: $RUN_HW"\n',
        '  exit 1\n',
        'fi\n',
        'if [ "$RUN_HLS" -eq 1 ]; then update_status hls attempted; fi\n',
        'if [ "$RUN_HLX" -eq 1 ]; then update_status hlx attempted; fi\n',
        'echo "Running Vivado HLS/HLX"\n',
        'run_status=0\n',
        'if [ "$DRY_RUN" -eq 1 ]; then\n',
        '  echo "DRY-RUN: (cd $HW_GEN_DIR/$ACC_TAG && BITSTREAM_SUFFIX=$BITSTREAM_SUFFIX $RUN_HW $RUN_HLS $RUN_HLX $OFFLOAD_HLS_HLX $COPY_BITS)"\n',
        'else\n',
        '  set +e\n',
        '  (cd "$HW_GEN_DIR/$ACC_TAG" && BITSTREAM_SUFFIX="$BITSTREAM_SUFFIX" "$RUN_HW" "$RUN_HLS" "$RUN_HLX" "$OFFLOAD_HLS_HLX" "$COPY_BITS")\n',
        '  run_status=$?\n',
        '  set -e\n',
        'fi\n',
        f'REPORT_DIR="$HW_GEN_DIR/$ACC_TAG/{settings["generated_files_dir"]}"\n',
        f'for report in {" ".join(settings["hlx_reports"])}; do\n',
        '  if [ -f "$REPORT_DIR/$report" ]; then\n',
        '    if [ "$DRY_RUN" -eq 1 ]; then\n',
        '      echo "DRY-RUN: cp $REPORT_DIR/$report $RESULTS_DIR/"\n',
        '    else\n',
        '      cp "$REPORT_DIR/$report" "$RESULTS_DIR/"\n',
        '    fi\n',
        '  fi\n',
        'done\n',
    ])
    _write_parse_hls_hlx_status(sh, 'HW_GEN_DIR', 'ACC_TAG')
    _write_lines(sh, [
        'if [ "$run_status" -ne 0 ]; then\n',
        '  echo "Vivado HLS/HLX failed (status $run_status); copying logs"\n',
        f'  for log in {" ".join(failure_logs)}; do\n',
        '    if [ -f "$HW_GEN_DIR/$ACC_TAG/$log" ]; then\n',
        '      if [ "$DRY_RUN" -eq 1 ]; then\n',
        '        echo "DRY-RUN: cp $HW_GEN_DIR/$ACC_TAG/$log $RESULTS_DIR/"\n',
        '      else\n',
        '        cp "$HW_GEN_DIR/$ACC_TAG/$log" "$RESULTS_DIR/"\n',
        '      fi\n',
        '    fi\n',
        '  done\n',
        '  exit "$run_status"\n',
        'fi\n',
        f'for log in {" ".join(settings["hw_gen_logs"])}; do\n',
        '  if [ -f "$HW_GEN_DIR/$ACC_TAG/$log" ]; then\n',
        '    if [ "$DRY_RUN" -eq 1 ]; then\n',
        '      echo "DRY-RUN: cp $HW_GEN_DIR/$ACC_TAG/$log $RESULTS_DIR/"\n',
        '    else\n',
        '      cp "$HW_GEN_DIR/$ACC_TAG/$log" "$RESULTS_DIR/"\n',
        '    fi\n',
        '  fi\n',
        'done\n',
        'if [ -n "$BITSTREAM_SRC" ]; then\n',
        '  mkdir -p "$(dirname "$BITSTREAM_SRC")"\n',
        '  if [ -f "$REPORT_DIR/$ACC_TAG.bit" ]; then\n',
        '    if [ "$DRY_RUN" -eq 1 ]; then\n',
        '      echo "DRY-RUN: cp $REPORT_DIR/$ACC_TAG.bit $BITSTREAM_SRC"\n',
        '    else\n',
        '      cp "$REPORT_DIR/$ACC_TAG.bit" "$BITSTREAM_SRC"\n',
        '    fi\n',
        '  fi\n',
        'fi\n',
        'if [ -n "$HWH_SRC" ]; then\n',
        '  mkdir -p "$(dirname "$HWH_SRC")"\n',
        '  if [ -f "$REPORT_DIR/$ACC_TAG.hwh" ]; then\n',
        '    if [ "$DRY_RUN" -eq 1 ]; then\n',
        '      echo "DRY-RUN: cp $REPORT_DIR/$ACC_TAG.hwh $HWH_SRC"\n',
        '    else\n',
        '      cp "$REPORT_DIR/$ACC_TAG.hwh" "$HWH_SRC"\n',
        '    fi\n',
        '  fi\n',
        'fi\n',
        'echo "Hardware generation completed. Use the run script to execute remote runs."\n',
    ])


def _write_remote_run_prelude(sh, settings: dict, run_id: str) -> None:
    _write_lines(sh, [
        '#!/usr/bin/env bash\n',
        'set -euo pipefail\n\n',
        'DRY_RUN="${DRY_RUN:-0}"\n',
        f'RUN_ID="{run_id}"\n',
        'export RUN_ID\n',
        'RUN_DIR="${RUN_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"\n',
        f'RESULTS_DIR="$RUN_DIR/{settings["results_dir_name"]}"\n',
        'REPO_ROOT="$RUN_DIR"\n',
        f'while [ ! -f "$REPO_ROOT/{settings["repo_root_marker"]}" ] && [ "$REPO_ROOT" != "/" ]; do\n',
        '  REPO_ROOT="$(dirname "$REPO_ROOT")"\n',
        'done\n',
        f'if [ ! -f "$REPO_ROOT/{settings["repo_root_marker"]}" ]; then\n',
        f'  echo "{settings["repo_root_marker"]} not found; cannot locate repo root"\n',
        '  exit 1\n',
        'fi\n\n',
    ])
    _write_update_status_function(sh, settings["status_filename"])


def _write_remote_helpers_section(sh) -> None:
    _write_lines(sh, [
        'mkdir -p "$RESULTS_DIR"\n\n',
        'EXP_KEY="$EXP_NAME"\n',
        'if [ -n "$EXP_VERSION" ]; then EXP_KEY="${EXP_KEY}_${EXP_VERSION}"; fi\n',
        'REMOTE_EXP_DIR="$BOARD_DIR/experiments/$EXP_KEY"\n\n',
        'remote_exec() {\n',
        '  local cmd="$*"\n',
        '  if [ "$DRY_RUN" -eq 1 ]; then\n',
        '    echo "DRY-RUN: ssh -p $BOARD_PORT $BOARD_USER@$BOARD_HOSTNAME $cmd"\n',
        '  else\n',
        '    ssh -t -q -p "$BOARD_PORT" "$BOARD_USER"@"$BOARD_HOSTNAME" "bash -i -c \\\"$cmd\\\""\n',
        '  fi\n',
        '}\n',
        'remote_rsync() {\n',
        '  if [ "$DRY_RUN" -eq 1 ]; then\n',
        '    echo "DRY-RUN: rsync -e ssh -p $BOARD_PORT $1 $BOARD_USER@$BOARD_HOSTNAME:$2"\n',
        '  else\n',
        '    rsync -q -r -avz -e "ssh -p $BOARD_PORT" "$1" "$BOARD_USER@$BOARD_HOSTNAME:$2"\n',
        '  fi\n',
        '}\n\n',
    ])


def _write_remote_execution_section(sh, settings: dict) -> None:
    _write_lines(sh, [
        'update_status bazel_build attempted\n',
        'BAZEL_START=$(date +%s)\n',
        'echo "Compiling $BAZEL_TARGET for $BOARD_NAME"\n',
        'bazel_ok=1\n',
        'if [ "$DRY_RUN" -eq 1 ]; then\n',
        '  echo "DRY-RUN: bazel6 build $BAZEL_TARGET ..."\n',
        'else\n',
        '  set +e\n',
        '  (\n',
        '    cd "$REPO_ROOT"\n',
        '    if [ "$BOARD_NAME" = "Z1" ]; then\n',
        '      bazel6 build "$BAZEL_TARGET" --cxxopt=\'-DACC_PROFILE\' --spawn_strategy=standalone --platforms=//platform:armv7_linux --@secda_tools//:config=fpga\n',
        '    elif [ "$BOARD_NAME" = "KRIA" ]; then\n',
        '      bazel6 build "$BAZEL_TARGET" --cxxopt=\'-DACC_PROFILE\' --spawn_strategy=standalone --platforms=//platform:aarch64_linux --@secda_tools//:config=fpga_arm64 --copt=\'-DKRIA\'\n',
        '    else\n',
        '      bazel6 build "$BAZEL_TARGET" --cxxopt=\'-DACC_PROFILE\' --spawn_strategy=standalone\n',
        '    fi\n',
        '  )\n',
        '  bazel_ok=$?\n',
        '  set -e\n',
        'fi\n',
        'BAZEL_END=$(date +%s)\n',
        'BAZEL_DUR=$((BAZEL_END - BAZEL_START))\n',
        'update_status bazel_build duration "$BAZEL_DUR"\n',
        'if [ "$bazel_ok" -ne 0 ] || [ ! -f "$BIN_PATH" ]; then\n',
        '  update_status bazel_build failure "${bazel_ok}"\n',
        '  echo "Binary not found or build failed: $BIN_PATH"\n',
        '  exit 1\n',
        'fi\n',
        'update_status bazel_build success 0\n\n',
        'echo "Preparing remote directories"\n',
        'remote_exec "mkdir -p $REMOTE_EXP_DIR/bins $REMOTE_EXP_DIR/bitstreams $REMOTE_EXP_DIR/exp $REMOTE_EXP_DIR/data $REMOTE_EXP_DIR/models"\n',
        f'remote_rsync "$REPO_ROOT/{settings["load_bitstream_script"]}" "$REMOTE_EXP_DIR/"\n',
        'remote_rsync "$BIN_PATH" "$REMOTE_EXP_DIR/bins/$BIN_NAME"\n',
        'remote_exec "chmod +x $REMOTE_EXP_DIR/bins/$BIN_NAME"\n',
        'if [ -f "$BITSTREAM_SRC" ]; then\n',
        '  remote_rsync "$BITSTREAM_SRC" "$REMOTE_EXP_DIR/bitstreams/$ACC_BIT"\n',
        'fi\n',
        'if [ -f "$HWH_SRC" ]; then\n',
        '  remote_rsync "$HWH_SRC" "$REMOTE_EXP_DIR/bitstreams/$ACC_HWH"\n',
        'fi\n\n',
        'echo "Mapping bitstream $ACC_BIT"\n',
        'if [ "$BOARD_NAME" = "KRIA" ]; then\n',
        '  remote_exec "cd $REMOTE_EXP_DIR/bitstreams && python3 $REMOTE_EXP_DIR/load_bitstream.py $ACC_BIT"\n',
        'else\n',
        '  remote_exec "cd $REMOTE_EXP_DIR/bitstreams && sudo python3 $REMOTE_EXP_DIR/load_bitstream.py $ACC_BIT"\n',
        'fi\n',
        'update_status remote_run attempted\n',
        'REMOTE_START=$(date +%s)\n',
        f'RUN_LOG="$RESULTS_DIR/{settings["run_log_name"].format(run_id="${RUN_ID}")}"\n',
        'REMOTE_LOG="$REMOTE_EXP_DIR/exp/${BIN_NAME}.log"\n',
        'LOCAL_REMOTE_LOG="$RESULTS_DIR/${BIN_NAME}.log"\n',
        'rm -f "$RUN_LOG" "$LOCAL_REMOTE_LOG"\n',
        'set +e\n',
        'remote_exec "mkdir -p $REMOTE_EXP_DIR/exp && cd $REMOTE_EXP_DIR && sudo chmod +x ./bins/$BIN_NAME && sudo ./bins/$BIN_NAME 2>&1 | tee $REMOTE_LOG" | tee "$RUN_LOG"\n',
        'remote_status=$?\n',
        'set -e\n',
        'echo "Summary: run_id=$RUN_ID board=$BOARD_NAME bin=$BIN_NAME" | tee -a "$RUN_LOG"\n',
        'echo "Summary: log=$RUN_LOG" | tee -a "$RUN_LOG"\n',
        'if [ "$DRY_RUN" -eq 1 ]; then\n',
        '  echo "DRY-RUN: rsync -e ssh -p $BOARD_PORT $BOARD_USER@$BOARD_HOSTNAME:$REMOTE_LOG $LOCAL_REMOTE_LOG"\n',
        'else\n',
        '  rsync -q -avz -e "ssh -p $BOARD_PORT" "$BOARD_USER@$BOARD_HOSTNAME:$REMOTE_LOG" "$LOCAL_REMOTE_LOG"\n',
        'fi\n',
        'REMOTE_END=$(date +%s)\n',
        'REMOTE_DUR=$((REMOTE_END - REMOTE_START))\n',
        'update_status remote_run duration "$REMOTE_DUR"\n',
        'if [ "$remote_status" -ne 0 ]; then\n',
        '  update_status remote_run failure "$remote_status"\n',
        'else\n',
        '  update_status remote_run success 0\n',
        'fi\n',
    ])


def _write_sim_run_prelude(sh, settings: dict, run_id: str) -> None:
    _write_lines(sh, [
        '#!/usr/bin/env bash\n',
        'set -euo pipefail\n\n',
        'DRY_RUN="${DRY_RUN:-0}"\n',
        f'RUN_ID="{run_id}"\n',
        'export RUN_ID\n',
        'RUN_DIR="${RUN_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"\n',
        f'RESULTS_DIR="$RUN_DIR/{settings["results_dir_name"]}"\n',
        'REPO_ROOT="$RUN_DIR"\n',
        f'while [ ! -f "$REPO_ROOT/{settings["repo_root_marker"]}" ] && [ "$REPO_ROOT" != "/" ]; do\n',
        '  REPO_ROOT="$(dirname "$REPO_ROOT")"\n',
        'done\n',
        f'if [ ! -f "$REPO_ROOT/{settings["repo_root_marker"]}" ]; then\n',
        f'  echo "{settings["repo_root_marker"]} not found; cannot locate repo root"\n',
        '  exit 1\n',
        'fi\n\n',
    ])
    _write_update_status_function(sh, settings["status_filename"])


def _write_sim_info_section(sh) -> None:
    _write_lines(sh, [
        'SIM_INFO=$(REPO_ROOT="$REPO_ROOT" RUN_DIR="$RUN_DIR" RUN_ID="$RUN_ID" python3 - <<\'PY\'\n',
        'import json, os, shlex\n',
        'from pathlib import Path\n',
        'repo_root = Path(os.environ.get("REPO_ROOT", ".")).resolve()\n',
        'run_dir = Path(os.environ.get("RUN_DIR", ".")).resolve()\n',
        'run_id = os.environ.get("RUN_ID", "").strip()\n',
        'data = json.loads((run_dir / "hw_params.json").read_text())\n',
        'hg = data.get("hardware_gen") or {}\n',
        'exp_name = hg.get("del")\n',
        'exp_version = f"v{hg.get(\'del_version\')}" if hg.get("del_version") is not None else ""\n',
        'suffix = f"_{run_id}" if run_id else ""\n',
        'bin_name = f"{exp_name}_{exp_version}{suffix}"\n',
        'try:\n',
        '    rel_path = run_dir.relative_to(repo_root)\n',
        '    path_to_exp = rel_path.as_posix()\n',
        'except ValueError:\n',
        '    path_to_exp = f"experiments/{exp_name}/{exp_version}"\n',
        'bazel_target = f"//{path_to_exp}:exp"\n',
        'bin_path = f"{repo_root}/bazel-bin/{path_to_exp}/exp"\n',
        'lines = {\n',
        '  "EXP_NAME": exp_name or "",\n',
        '  "EXP_VERSION": exp_version,\n',
        '  "BIN_NAME": bin_name,\n',
        '  "BAZEL_TARGET": bazel_target,\n',
        '  "BIN_PATH": bin_path,\n',
        '}\n',
        'for k, v in lines.items():\n',
        '    print(f"{k}={shlex.quote(str(v))}")\n',
        'PY\n',
        ')\n',
        'eval "$SIM_INFO"\n',
        'if [ -z "$BAZEL_TARGET" ] || [ -z "$BIN_PATH" ]; then\n',
        '  echo "Unable to resolve simulation target from hw_params.json"\n',
        '  exit 1\n',
        'fi\n\n',
    ])


def _write_sim_execution_section(sh, settings: dict) -> None:
    _write_lines(sh, [
        'mkdir -p "$RESULTS_DIR"\n\n',
        'update_status sim_build attempted\n',
        'SIM_BUILD_START=$(date +%s)\n',
        'echo "Building SystemC simulation for $BAZEL_TARGET"\n',
        'sim_build_status=0\n',
        'if [ "$DRY_RUN" -eq 1 ]; then\n',
        '  echo "DRY-RUN: bazel6 build $BAZEL_TARGET -c dbg --cxxopt=\'-DSYSC\' --cxxopt=\'-DACC_PROFILE\' --spawn_strategy=standalone --platforms=//platform:linux_x64 --@secda_tools//:config=sysc"\n',
        'else\n',
        '  set +e\n',
        '  (\n',
        '    cd "$REPO_ROOT"\n',
        '    bazel6 build "$BAZEL_TARGET" -c dbg --cxxopt=\'-DSYSC\' --cxxopt=\'-DACC_PROFILE\' --spawn_strategy=standalone --platforms=//platform:linux_x64 --@secda_tools//:config=sysc\n',
        '  )\n',
        '  sim_build_status=$?\n',
        '  set -e\n',
        'fi\n',
        'SIM_BUILD_END=$(date +%s)\n',
        'SIM_BUILD_DUR=$((SIM_BUILD_END - SIM_BUILD_START))\n',
        'update_status sim_build duration "$SIM_BUILD_DUR"\n',
        'if [ "$sim_build_status" -ne 0 ] || [ ! -f "$BIN_PATH" ]; then\n',
        '  update_status sim_build failure "$sim_build_status"\n',
        '  echo "Simulation binary not found or build failed: $BIN_PATH"\n',
        '  exit 1\n',
        'fi\n',
        'update_status sim_build success 0\n\n',
        'update_status sim_run attempted\n',
        'SIM_RUN_START=$(date +%s)\n',
        f'SIM_RUN_LOG="$RESULTS_DIR/{settings["sim_run_log_name"].format(run_id="${RUN_ID}")}"\n',
        'SIM_LOG="$RESULTS_DIR/${BIN_NAME}_sim.log"\n',
        'rm -f "$SIM_RUN_LOG" "$SIM_LOG"\n',
        'echo "Running local SystemC simulation binary: $BIN_PATH"\n',
        'set +e\n',
        'if [ "$DRY_RUN" -eq 1 ]; then\n',
        '  echo "DRY-RUN: (cd $REPO_ROOT && $BIN_PATH)" | tee "$SIM_RUN_LOG"\n',
        '  sim_run_status=0\n',
        'else\n',
        '  (\n',
        '    cd "$REPO_ROOT"\n',
        '    "$BIN_PATH"\n',
        '  ) 2>&1 | tee "$SIM_LOG" | tee "$SIM_RUN_LOG"\n',
        '  sim_run_status=${PIPESTATUS[0]}\n',
        'fi\n',
        'set -e\n',
        'echo "Summary: run_id=$RUN_ID mode=sim bin=$BIN_NAME" | tee -a "$SIM_RUN_LOG"\n',
        'echo "Summary: sim_log=$SIM_LOG" | tee -a "$SIM_RUN_LOG"\n',
        'SIM_RUN_END=$(date +%s)\n',
        'SIM_RUN_DUR=$((SIM_RUN_END - SIM_RUN_START))\n',
        'update_status sim_run duration "$SIM_RUN_DUR"\n',
        'if [ "$sim_run_status" -ne 0 ]; then\n',
        '  update_status sim_run failure "$sim_run_status"\n',
        '  exit "$sim_run_status"\n',
        'fi\n',
        'update_status sim_run success 0\n',
        'echo "Simulation completed. Results captured under $RESULTS_DIR"\n',
    ])


def write_hw_gen_script(script_path: Path, settings: dict, run_id: str, run_name: str, failure_logs: list[str]) -> None:
    with script_path.open('w') as sh:
        _write_hw_gen_prelude(sh, settings, run_id, run_name, failure_logs)
        _write_hw_gen_config_section(sh, settings, run_id, run_name)
        _write_board_info_section(sh, settings)
        _write_hw_gen_execution_section(sh, settings, failure_logs)

    script_path.chmod(0o755)


def write_remote_run_script(script_path: Path, settings: dict, run_id: str) -> None:
    with script_path.open('w') as sh:
        _write_remote_run_prelude(sh, settings, run_id)
        _write_board_info_section(sh, settings)
        _write_remote_helpers_section(sh)
        _write_remote_execution_section(sh, settings)

    script_path.chmod(0o755)


def write_sim_run_script(script_path: Path, settings: dict, run_id: str) -> None:
    with script_path.open('w') as sh:
        _write_sim_run_prelude(sh, settings, run_id)
        _write_sim_info_section(sh)
        _write_sim_execution_section(sh, settings)

    script_path.chmod(0o755)


def write_batch_script(script_path: Path, glob_pattern: str, missing_message: str, intro_message: str) -> None:
    with script_path.open('w') as sh:
        sh.write('#!/usr/bin/env bash\n')
        sh.write('set -euo pipefail\n\n')
        sh.write('RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n')
        sh.write(f'echo "{intro_message} under $RUN_DIR"\n')
        sh.write('ARGS=("$@")\n')
        sh.write('found=0\n')
        sh.write('failures=0\n')
        sh.write('failed_ids=()\n')
        sh.write(f'for script in "$RUN_DIR"/*/{glob_pattern}; do\n')
        sh.write('  if [ -f "$script" ]; then\n')
        sh.write('    found=1\n')
        sh.write('    echo "=== $script ==="\n')
        sh.write('    set +e\n')
        sh.write('    run_dir="$(dirname "$script")"\n')
        sh.write('    (cd "$run_dir" && "$script" "${ARGS[@]}")\n')
        sh.write('    status=$?\n')
        sh.write('    set -e\n')
        sh.write('    if [ "$status" -ne 0 ]; then\n')
        sh.write('      echo "Script failed ($status): $script"\n')
        sh.write('      failures=$((failures+1))\n')
        sh.write('      failed_ids+=("$(basename "$run_dir")")\n')
        sh.write('    fi\n')
        sh.write('  fi\n')
        sh.write('done\n')
        sh.write('if [ "$found" -eq 0 ]; then\n')
        sh.write(f'  echo "{missing_message} under $RUN_DIR"\n')
        sh.write('  exit 1\n')
        sh.write('fi\n')
        sh.write('if [ "$failures" -ne 0 ]; then\n')
        sh.write('  echo "$failures script(s) failed"\n')
        sh.write('  echo "Failed run IDs: ${failed_ids[*]}"\n')
        sh.write('  exit 1\n')
        sh.write('fi\n')

    script_path.chmod(0o755)


def write_collect_results_script(script_path: Path, settings: dict) -> None:
    with script_path.open('w') as sh:
        sh.write('#!/usr/bin/env bash\n')
        sh.write('set -euo pipefail\n\n')
        sh.write('RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n')
        sh.write('EXP_KEY="$(basename "$RUN_DIR")"\n')
        sh.write(f'OUT_DIR="$RUN_DIR/{settings["collected_results_dir"]}/$EXP_KEY"\n')
        sh.write(f'RES_DIR_NAME="{settings["results_dir_name"]}"\n')
        sh.write('mkdir -p "$OUT_DIR"\n')
        sh.write('found=0\n')
        sh.write('for run_dir in "$RUN_DIR"/*; do\n')
        sh.write('  if [ -d "$run_dir" ] && [ -d "$run_dir/$RES_DIR_NAME" ]; then\n')
        sh.write('    found=1\n')
        sh.write('    run_id="$(basename "$run_dir")"\n')
        sh.write('    dest="$OUT_DIR/$run_id"\n')
        sh.write('    rm -rf "$dest"\n')
        sh.write('    mkdir -p "$dest"\n')
        sh.write('    cp -a "$run_dir/$RES_DIR_NAME/." "$dest/"\n')
        sh.write('  fi\n')
        sh.write('done\n')
        sh.write('if [ "$found" -eq 0 ]; then\n')
        sh.write('  echo "No results directories found under $RUN_DIR"\n')
        sh.write('  exit 1\n')
        sh.write('fi\n')
        sh.write('echo "Collected results under $OUT_DIR"\n')

    script_path.chmod(0o755)


def write_collect_dataset_script(script_path: Path, settings: dict) -> None:
    with script_path.open('w') as sh:
        sh.write('#!/usr/bin/env bash\n')
        sh.write('set -euo pipefail\n\n')
        sh.write('RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n')
        sh.write(f'DATASET_DIR="$RUN_DIR/{settings["dataset_dir"]}"\n')
        sh.write(f'RUNS_DIR="$DATASET_DIR/{settings["dataset_runs_dir"]}"\n')
        sh.write('mkdir -p "$RUNS_DIR"\n')
        sh.write('found=0\n')
        sh.write('for run_dir in "$RUN_DIR"/*; do\n')
        sh.write('  if [ -d "$run_dir" ] && [ -d "$run_dir/accelerator" ]; then\n')
        sh.write('    found=1\n')
        sh.write('    run_id="$(basename "$run_dir")"\n')
        sh.write('    dest="$RUNS_DIR/$run_id"\n')
        sh.write('    rm -rf "$dest"\n')
        sh.write('    mkdir -p "$dest/sources" "$dest/results"\n')
        sh.write('    cp -a "$run_dir/accelerator" "$dest/sources/"\n')
        sh.write('    if [ -f "$run_dir/experiment.cc" ]; then\n')
        sh.write('      cp "$run_dir/experiment.cc" "$dest/sources/"\n')
        sh.write('    fi\n')
        sh.write('    if [ -f "$run_dir/experiment.h" ]; then\n')
        sh.write('      cp "$run_dir/experiment.h" "$dest/sources/"\n')
        sh.write('    fi\n')
        sh.write('    if [ -d "$run_dir/results" ]; then\n')
        sh.write('      cp -a "$run_dir/results/." "$dest/results/"\n')
        sh.write('    fi\n')
        sh.write('  fi\n')
        sh.write('done\n')
        sh.write('if [ "$found" -eq 0 ]; then\n')
        sh.write('  echo "No run directories with accelerator sources found under $RUN_DIR"\n')
        sh.write('  exit 1\n')
        sh.write('fi\n')
        sh.write(f'if [ -f "$RUN_DIR/{settings["runs_csv"]}" ]; then\n')
        sh.write(f'  cp "$RUN_DIR/{settings["runs_csv"]}" "$DATASET_DIR/"\n')
        sh.write('fi\n')
        sh.write('echo "Dataset created under $DATASET_DIR"\n')
        sh.write(f'python3 {settings["parse_hardware_script"]} --dataset "$DATASET_DIR"\n')
        sh.write(f'python3 {settings["parse_performance_script"]} --dataset "$DATASET_DIR"\n')

    script_path.chmod(0o755)
