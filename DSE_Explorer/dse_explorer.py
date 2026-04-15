#!/usr/bin/env python3
"""DSE Explorer

Generate experiment copies and per-run bash driver scripts from hw_params.json.
Supports new hw_params format with 'groups' and 'parameters'.
Groups are paired index-wise (no permutation inside a group).
Ungrouped parameters are cross-producted.

Usage:
    python3 DSE_Explorer/dse_explorer.py --experiment experiments/mm_exp/v1 --dry-run
        python3 DSE_Explorer/dse_explorer.py --experiment experiments/mm_exp/v1 --settings DSE_Explorer/dse_setting.json
"""

from pathlib import Path
import argparse
import json
import itertools
import shutil
import re
import os
import csv
import datetime
import math


REQUIRED_SETTINGS = [
    "output_root",
    "experiment_folder_format",
    "runs_csv",
    "hw_params_filename",
    "results_dir_name",
    "hw_gen_dir_name",
    "generated_files_dir",
    "hw_config_filename",
    "manifest_filename",
    "config_json",
    "hw_gen_script",
    "load_bitstream_script",
    "run_log_name",
    "hw_gen_script_template",
    "run_script_template",
    "hw_gen_glob",
    "run_glob",
    "hw_gen_all_name",
    "run_all_name",
    "collect_results_name",
    "collected_results_dir",
    "collect_dataset_name",
    "dataset_dir",
    "dataset_runs_dir",
    "repo_root_marker",
    "hlx_reports",
    "hw_gen_logs",
    "artifact_suffix_format",
    "status_filename",
]


def load_settings(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"DSE settings file not found: {path}")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("dse_setting.json must be an object")
    missing = [key for key in REQUIRED_SETTINGS if key not in data]
    if missing:
        raise ValueError(f"dse_setting.json missing required keys: {', '.join(missing)}")
    return data


def format_experiment_folder(source_exp: Path, fmt: str) -> str:
    mapping = {
        "exp_name": source_exp.parent.name,
        "exp_version": source_exp.name,
        "experiment": source_exp.name,
        "exp_path": source_exp.as_posix(),
    }
    try:
        return fmt.format_map(mapping)
    except KeyError as exc:
        raise ValueError(f"Unknown placeholder {exc} in experiment_folder_format") from exc


def find_repo_root(start: Path, marker: str) -> Path | None:
    current = start.resolve()
    while True:
        if (current / marker).exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def update_build_files(run_dir: Path, repo_root: Path, source_rel: str) -> None:
    run_rel = run_dir.relative_to(repo_root).as_posix()
    pattern = re.compile(rf"//{re.escape(source_rel)}(?P<sep>[:/])")
    replacement = f"//{run_rel}" + r"\g<sep>"
    replace_pairs = {
        f"//{source_rel}/": f"//{run_rel}/",
        f"//{source_rel}:": f"//{run_rel}:",
    }
    for build_path in list(run_dir.rglob("BUILD")) + list(run_dir.rglob("BUILD.bazel")):
        text = build_path.read_text()
        updated = text
        for old, new in replace_pairs.items():
            updated = updated.replace(old, new)
        updated = pattern.sub(replacement, updated)
        if updated != text:
            build_path.write_text(updated)


def rewrite_build_deps_for_runs(out_root: Path, source_exp: Path, settings: dict) -> None:
    build_repo_root = find_repo_root(out_root, settings["repo_root_marker"])
    if not build_repo_root:
        return
    source_rel_candidates = [f"experiments/{source_exp.parent.name}/{source_exp.name}"]
    try:
        source_rel_candidates.insert(0, source_exp.relative_to(build_repo_root).as_posix())
    except ValueError:
        pass
    for run_dir in out_root.iterdir():
        if not run_dir.is_dir():
            continue
        for source_rel in dict.fromkeys(source_rel_candidates):
            try:
                update_build_files(run_dir, build_repo_root, source_rel)
            except ValueError:
                continue


def load_hw_params(path: Path):
    with path.open() as f:
        hw = json.load(f)
    # Some hw_params.json files nest DSE settings under a top-level 'DSE' key
    if isinstance(hw, dict) and 'DSE' in hw:
        hw = hw['DSE']

    if isinstance(hw, dict) and 'parameters' in hw and 'groups' in hw:
        params_map = {}
        for d in hw.get('parameters', []):
            if isinstance(d, dict):
                for k, v in d.items():
                    params_map[k] = v
        groups_raw = hw.get('groups', [])
        groups = []
        for g in groups_raw:
            if isinstance(g, dict):
                for _, names in g.items():
                    groups.append(list(names))
    else:
        # legacy flat maps name -> list
        if not isinstance(hw, dict):
            raise ValueError("hw_params.json must be an object mapping parameter names to arrays or have 'parameters' and 'groups')")
        params_map = hw
        groups = []

    return params_map, groups


def build_group_choices(params_map, groups):
    all_params = set(params_map.keys())
    grouped = set()
    group_choices = []

    for g in groups:
        if not g:
            continue
        missing = [p for p in g if p not in params_map]
        if missing:
            raise KeyError(f"Parameters {missing} declared in a group but not found in 'parameters'")
        lengths = [len(params_map[p]) for p in g]
        if len(set(lengths)) != 1:
            raise ValueError(f"Parameters in group {g} have differing lengths: {lengths}")
        group_len = lengths[0]
        choices = []
        for i in range(group_len):
            mapping = {p: params_map[p][i] for p in g}
            choices.append(mapping)
        group_choices.append(choices)
        grouped.update(g)

    # ungrouped params -> each becomes its own choice list
    ungrouped = sorted(list(all_params - grouped))
    for p in ungrouped:
        choices = [{p: v} for v in params_map[p]]
        group_choices.append(choices)

    return group_choices


# ---------------------------------------------------------------------------
# Parameter replacement patterns – searched in priority order (first match wins).
# Each entry is a tuple of:
#   (label, regex_template, replacement_template)
#
# In the templates the literal {NAME} is substituted with the escaped
# parameter name at runtime.
#
# regex_template  – must contain exactly ONE capturing group around the
#                   *value* to replace and use a raw-string.
#                   Group layout: (prefix)(value)(suffix)
# replacement_template – a format-string producing the full replacement;
#                        receives {prefix}, {value} and {suffix}.
#
# Re-order, add or remove entries here to change search priorities.
# ---------------------------------------------------------------------------
PARAM_REPLACE_PATTERNS: list[tuple[str, str, str]] = [
    # 1) #define NAME <value>
    (
        "#define",
        r'(#\s*define\s+{NAME}\s+)([-+]?\d+)(\b)',
        r'\g<1>{VALUE}\g<3>',
    ),
    # 2) const int NAME = <value>;
    (
        "const int",
        r'(\bconst\b\s+\bint\b\s+{NAME}\s*=\s*)([-+]?\d+)(\s*;)',
        r'\g<1>{VALUE}\g<3>',
    ),
    # 3) int NAME = <value>;
    (
        "int",
        r'(\bint\b\s+{NAME}\s*=\s*)([-+]?\d+)(\s*;)',
        r'\g<1>{VALUE}\g<3>',
    ),
]


def _write_update_status_function(sh, status_filename: str):
    """Write a bash helper ``update_status`` into *sh*.

    Usage inside generated scripts::

        update_status <stage> attempted              # mark attempted + timestamp
        update_status <stage> success [exit_code]    # mark success=true
        update_status <stage> failure [exit_code]    # mark success=false
        update_status <stage> duration <seconds>     # record duration

    The function uses an inline Python snippet to atomically read/merge/write
    the JSON status file so concurrent calls are safe.
    """
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


def replace_params_in_file(path: Path, mapping: dict,
                           patterns: list[tuple[str, str, str]] | None = None):
    """Replace parameter values in *path* using a priority-ordered pattern list.

    For each parameter the patterns are tried top-to-bottom; the first one that
    produces at least one substitution wins and the remaining patterns are
    skipped for that parameter.
    """
    if patterns is None:
        patterns = PARAM_REPLACE_PATTERNS

    text = path.read_text()
    new_text = text
    for name, val in mapping.items():
        str_val = str(int(val))
        for _label, regex_tpl, _repl_tpl in patterns:
            compiled = re.compile(regex_tpl.replace("{NAME}", re.escape(name)))
            repl = _repl_tpl.replace("{VALUE}", str_val)
            new_text, count = compiled.subn(repl, new_text)
            if count > 0:
                break  # first matching pattern wins for this parameter
    path.write_text(new_text)


def generate_runs(source_exp: Path, hw_json: Path, out_root: Path, settings: dict, dry_run: bool = False, sample: int = 0):
    repo_root = find_repo_root(source_exp, settings["repo_root_marker"])
    source_rel_candidates = [f"experiments/{source_exp.parent.name}/{source_exp.name}"]
    if repo_root:
        try:
            source_rel_candidates.insert(0, source_exp.relative_to(repo_root).as_posix())
        except ValueError:
            pass
    params_map, groups = load_hw_params(hw_json)
    group_choices = build_group_choices(params_map, groups)

    combos = list(itertools.product(*group_choices))
    total = len(combos)
    if sample and 0 < sample < total:
        step = total / sample
        indices = []
        for i in range(sample):
            idx = int(math.floor(i * step))
            if idx < 0:
                idx = 0
            if idx >= total:
                idx = total - 1
            indices.append(idx)
        indices = sorted(dict.fromkeys(indices))
        combos = [combos[i] for i in indices]
        print(f"Sampling {len(combos)} of {total} run(s)")
    else:
        print(f"Found {total} run(s) to generate")

    # Build CSV
    out_root.mkdir(parents=True, exist_ok=True)
    csv_path = out_root / settings["runs_csv"]
    fieldnames = ["run_id", "run_name", "params", "source_experiment"]

    timestamp = datetime.datetime.utcnow().strftime("%y%m%dT%H%M")

    run_scripts = []

    with csv_path.open('w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for idx, combo in enumerate(combos, start=1):
            mapping = {}
            for d in combo:
                mapping.update(d)
            suffix = "_".join(f"{k}{mapping[k]}" for k in sorted(mapping.keys()))
            run_uid = f"{idx:04d}"
            run_name = run_uid
            run_id = f"{timestamp}_{run_uid}"
            dest_dir = out_root / run_name

            if dry_run:
                print(f"DRY-RUN: would create {dest_dir}")
            else:
                if dest_dir.exists():
                    shutil.rmtree(dest_dir)
                shutil.copytree(source_exp, dest_dir)

                build_repo_root = repo_root or find_repo_root(dest_dir, settings["repo_root_marker"])
                if build_repo_root:
                    for source_rel in dict.fromkeys(source_rel_candidates):
                        try:
                            update_build_files(dest_dir, build_repo_root, source_rel)
                        except ValueError:
                            continue
                    try:
                        run_rel = dest_dir.relative_to(build_repo_root).as_posix()
                        hard_old = f"//experiments/{source_exp.parent.name}/{source_exp.name}"
                        hard_new = f"//{run_rel}"
                        for build_path in list(dest_dir.rglob("BUILD")) + list(dest_dir.rglob("BUILD.bazel")):
                            text = build_path.read_text()
                            updated = text.replace(hard_old + "/", hard_new + "/").replace(hard_old + ":", hard_new + ":")
                            if updated != text:
                                build_path.write_text(updated)
                    except ValueError:
                        pass

                # find acc_config.*
                acc_files = list(dest_dir.rglob('acc_config.sc.h')) or list(dest_dir.rglob('acc_config.h'))
                if not acc_files:
                    print(f"WARNING: no acc_config file found in {dest_dir}")
                else:
                    replace_params_in_file(acc_files[0], mapping)

                # write main run script (hardware + optional remote)
                run_sh = dest_dir / settings["hw_gen_script_template"].format(run_id=run_id)
                run_remote_sh = dest_dir / settings["run_script_template"].format(run_id=run_id)
                failure_logs = list(dict.fromkeys(settings["hw_gen_logs"] + ["vivado_hls.log", "vivado.log"]))
                with run_sh.open('w') as sh:
                    sh.write("#!/usr/bin/env bash\n")
                    sh.write("set -euo pipefail\n\n")
                    sh.write(f'RUN_ID="{run_id}"\n')
                    sh.write('export RUN_ID\n')
                    sh.write(f'RUN_NAME="{run_name}"\n')
                    sh.write('RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n')
                    sh.write(f'RESULTS_DIR="$RUN_DIR/{settings["results_dir_name"]}"\n')
                    sh.write('REPO_ROOT="$RUN_DIR"\n')
                    sh.write(f'while [ ! -f "$REPO_ROOT/{settings["repo_root_marker"]}" ] && [ "$REPO_ROOT" != "/" ]; do\n')
                    sh.write('  REPO_ROOT="$(dirname "$REPO_ROOT")"\n')
                    sh.write('done\n')
                    sh.write(f'if [ ! -f "$REPO_ROOT/{settings["repo_root_marker"]}" ]; then\n')
                    sh.write(f'  echo "{settings["repo_root_marker"]} not found; cannot locate repo root"\n')
                    sh.write('  exit 1\n')
                    sh.write('fi\n\n')
                    sh.write('mkdir -p "$RESULTS_DIR"\n\n')
                    sh.write(f'for report in {" ".join(settings["hlx_reports"])}; do rm -f "$RESULTS_DIR/$report"; done\n')
                    sh.write(f'for log in {" ".join(failure_logs)}; do rm -f "$RESULTS_DIR/$log"; done\n\n')
                    sh.write(f'HW_GEN_DIR="$RUN_DIR/{settings["hw_gen_dir_name"]}"\n')
                    sh.write(f'CONFIG_PATH="$HW_GEN_DIR/{settings["hw_config_filename"]}"\n')
                    sh.write(f'MANIFEST_PATH="$HW_GEN_DIR/{settings["manifest_filename"]}"\n')
                    sh.write('mkdir -p "$HW_GEN_DIR"\n\n')
                    _write_update_status_function(sh, settings["status_filename"])
                    sh.write('RUN_HLS="${RUN_HLS:-1}"\n')
                    sh.write('RUN_HLX="${RUN_HLX:-1}"\n')
                    sh.write('OFFLOAD_HLS_HLX="${OFFLOAD_HLS_HLX:-0}"\n')
                    sh.write('COPY_BITS="${COPY_BITS:-1}"\n')
                    sh.write('FORCE_HW_GEN="${FORCE_HW_GEN:-0}"\n')
                    sh.write('DRY_RUN="${DRY_RUN:-0}"\n')
                    suffix = settings["artifact_suffix_format"].format(run_id=run_id)
                    sh.write(f'BITSTREAM_SUFFIX="${{BITSTREAM_SUFFIX:-{suffix}}}"\n')
                    sh.write('if [ $# -ge 1 ]; then RUN_HLS="$1"; fi\n')
                    sh.write('if [ $# -ge 2 ]; then RUN_HLX="$2"; fi\n')
                    sh.write('if [ $# -ge 3 ]; then OFFLOAD_HLS_HLX="$3"; fi\n')
                    sh.write('if [ $# -ge 4 ]; then COPY_BITS="$4"; fi\n\n')
                    sh.write('ACC_TAG=$(CONFIG_PATH="$CONFIG_PATH" python3 - <<\'PY\'\n')
                    sh.write('import json, os, sys\n')
                    sh.write('from pathlib import Path\n')
                    sh.write('cfg = os.environ.get("CONFIG_PATH", "hardware_gen/hw_config.json")\n')
                    sh.write('run_dir = Path(".").resolve()\n')
                    sh.write(f'hw_path = Path("{settings["hw_params_filename"]}")\n')
                    sh.write('if not hw_path.exists():\n')
                    sh.write('    print("", end="")\n')
                    sh.write('    sys.exit(2)\n')
                    sh.write('data = json.load(hw_path.open())\n')
                    sh.write('hg = data.get("hardware_gen")\n')
                    sh.write('if not isinstance(hg, dict):\n')
                    sh.write('    print("", end="")\n')
                    sh.write('    sys.exit(3)\n')
                    sh.write('run_id = os.environ.get("RUN_ID", "").strip()\n')
                    sh.write('acc_name = str(hg.get("acc_name", ""))\n')
                    sh.write('if run_id:\n')
                    sh.write('    acc_name = f"{acc_name}_{run_id}" if acc_name else run_id\n')
                    sh.write('hg["acc_name"] = acc_name\n')
                    sh.write('acc_src = (run_dir / "accelerator").resolve()\n')
                    sh.write('acc_link = (run_dir / "' + settings["hw_gen_dir_name"] + '" / "acc_link").resolve()\n')
                    sh.write('hg["acc_src"] = str(acc_src)\n')
                    sh.write('hg["acc_link_folder"] = str(acc_link)\n')
                    sh.write('Path(cfg).parent.mkdir(parents=True, exist_ok=True)\n')
                    sh.write('with open(cfg, "w") as f:\n')
                    sh.write('    json.dump(hg, f, indent=2)\n')
                    sh.write('acc_tag = f"{acc_name}_{hg.get(\'acc_version\')}_{hg.get(\'acc_sub_version\')}"\n')
                    sh.write('print(acc_tag)\n')
                    sh.write('PY\n')
                    sh.write(')\n')
                    sh.write('if [ -z "$ACC_TAG" ] || [ ! -f "$CONFIG_PATH" ]; then\n')
                    sh.write('  echo "hardware_gen config missing in hw_params.json"\n')
                    sh.write('  exit 1\n')
                    sh.write('fi\n\n')
                    sh.write('ACC_TAG_ENV="$ACC_TAG" python3 - <<\'PY\'\n')
                    sh.write('import json, os\n')
                    sh.write('from pathlib import Path\n')
                    sh.write('run_id = "' + run_id + '"\n')
                    sh.write('run_name = "' + run_name + '"\n')
                    sh.write('acc_tag = os.environ.get("ACC_TAG_ENV", "")\n')
                    sh.write('manifest = {\n')
                    sh.write('  "run_id": run_id,\n')
                    sh.write('  "run_name": run_name,\n')
                    sh.write(f'  "hw_params": str(Path("{settings["hw_params_filename"]}").resolve()),\n')
                    sh.write(f'  "hardware_gen_dir": str(Path("{settings["hw_gen_dir_name"]}").resolve()),\n')
                    sh.write('  "acc_tag": acc_tag\n')
                    sh.write('}\n')
                    sh.write(f'Path("{settings["hw_gen_dir_name"]}/{settings["manifest_filename"]}").write_text(json.dumps(manifest, indent=2))\n')
                    sh.write('PY\n\n')
                    sh.write('BOARD_INFO=$(REPO_ROOT="$REPO_ROOT" python3 - <<\'PY\'\n')
                    sh.write('import json, os, shlex\n')
                    sh.write('from pathlib import Path\n')
                    sh.write('repo_root = Path(os.environ.get("REPO_ROOT", ".")).resolve()\n')
                    sh.write('run_dir = Path(os.environ.get("RUN_DIR", ".")).resolve()\n')
                    sh.write(f'config_path = repo_root / "{settings["config_json"]}"\n')
                    sh.write(f'data = json.load(open("{settings["hw_params_filename"]}"))\n')
                    sh.write('hg = data.get("hardware_gen") or {}\n')
                    sh.write('cfg = json.load(config_path.open())\n')
                    sh.write('boards = cfg.get("boards", {})\n')
                    sh.write('board_name = hg.get("board") or (list(boards.keys())[0] if boards else "")\n')
                    sh.write('b = boards.get(board_name, {})\n')
                    sh.write(f'suffix_fmt = {json.dumps(settings["artifact_suffix_format"])}\n')
                    sh.write('run_id = os.environ.get("RUN_ID", "")\n')
                    sh.write('suffix = suffix_fmt.format(run_id=run_id) if run_id else ""\n')
                    sh.write('exp_name = hg.get("del")\n')
                    sh.write('exp_version = f"v{hg.get(\'del_version\')}" if hg.get("del_version") is not None else ""\n')
                    sh.write('acc_name = hg.get("acc_name")\n')
                    sh.write('if run_id and acc_name:\n')
                    sh.write('    acc_name = f"{acc_name}_{run_id}"\n')
                    sh.write('acc_version = hg.get("acc_version")\n')
                    sh.write('acc_sub = hg.get("acc_sub_version")\n')
                    sh.write('acc_bit = f"{acc_name}_{acc_version}_{acc_sub}{suffix}.bit"\n')
                    sh.write('acc_hwh = f"{acc_name}_{acc_version}_{acc_sub}{suffix}.hwh"\n')
                    sh.write('bin_name = f"{exp_name}_{exp_version}{suffix}"\n')
                    sh.write('try:\n')
                    sh.write('    rel_path = run_dir.relative_to(repo_root)\n')
                    sh.write('    path_to_exp = rel_path.as_posix()\n')
                    sh.write('except ValueError:\n')
                    sh.write('    path_to_exp = f"experiments/{exp_name}/{exp_version}"\n')
                    sh.write('bazel_target = f"//{path_to_exp}:exp"\n')
                    sh.write('bin_path = f"{repo_root}/bazel-bin/{path_to_exp}/exp"\n')
                    sh.write('bitstream_dir = cfg.get("bitstream_dir") or str(repo_root / "hardware_automation" / "bitstreams")\n')
                    sh.write('bit_src = f"{bitstream_dir}/{board_name}/{acc_bit}"\n')
                    sh.write('hwh_src = f"{bitstream_dir}/{board_name}/{acc_hwh}"\n')
                    sh.write('data_dir = cfg.get("data_dir", "")\n')
                    sh.write('models_dirs = cfg.get("models_dirs", [])\n')
                    sh.write('lines = {\n')
                    sh.write('  "BOARD_NAME": board_name,\n')
                    sh.write('  "BOARD_USER": b.get("board_user", ""),\n')
                    sh.write('  "BOARD_HOSTNAME": b.get("board_hostname", ""),\n')
                    sh.write('  "BOARD_PORT": str(b.get("board_port", "")),\n')
                    sh.write('  "BOARD_DIR": b.get("board_dir", ""),\n')
                    sh.write('  "EXP_NAME": exp_name or "",\n')
                    sh.write('  "EXP_VERSION": exp_version,\n')
                    sh.write('  "BIN_NAME": bin_name,\n')
                    sh.write('  "BAZEL_TARGET": bazel_target,\n')
                    sh.write('  "BIN_PATH": bin_path,\n')
                    sh.write('  "ACC_BIT": acc_bit,\n')
                    sh.write('  "ACC_HWH": acc_hwh,\n')
                    sh.write('  "BITSTREAM_SRC": bit_src,\n')
                    sh.write('  "HWH_SRC": hwh_src,\n')
                    sh.write('  "DATA_DIR": data_dir,\n')
                    sh.write('  "MODELS_DIRS": ":".join(models_dirs),\n')
                    sh.write('}\n')
                    sh.write('for k, v in lines.items():\n')
                    sh.write('    print(f"{k}={shlex.quote(str(v))}")\n')
                    sh.write('PY\n')
                    sh.write(')\n')
                    sh.write('eval "$BOARD_INFO"\n')
                    sh.write('if [ -z "$BOARD_NAME" ] || [ -z "$BOARD_USER" ] || [ -z "$BOARD_HOSTNAME" ] || [ -z "$BOARD_DIR" ]; then\n')
                    sh.write('  echo "Board configuration missing in config.json"\n')
                    sh.write('  exit 1\n')
                    sh.write('fi\n\n')
                    sh.write('if [ -d "$HW_GEN_DIR/$ACC_TAG" ] && [ "$FORCE_HW_GEN" -ne 1 ]; then\n')
                    sh.write('  echo "hardware_gen exists; skipping hw_gen.py (set FORCE_HW_GEN=1 to regenerate)"\n')
                    sh.write('else\n')
                    sh.write('  echo "Generating hardware project in $HW_GEN_DIR"\n')
                    sh.write('  if [ "$DRY_RUN" -eq 1 ]; then\n')
                    sh.write(f'    echo "DRY-RUN: SECDA_OUT_DIR=$HW_GEN_DIR python3 $REPO_ROOT/{settings["hw_gen_script"]} $CONFIG_PATH"\n')
                    sh.write('  else\n')
                    sh.write(f'    SECDA_OUT_DIR="$HW_GEN_DIR" python3 "$REPO_ROOT/{settings["hw_gen_script"]}" "$CONFIG_PATH"\n')
                    sh.write('  fi\n')
                    sh.write('fi\n\n')
                    sh.write('RUN_HW="$HW_GEN_DIR/$ACC_TAG/run.sh"\n')
                    sh.write('if [ ! -x "$RUN_HW" ]; then\n')
                    sh.write('  echo "Hardware run script not found: $RUN_HW"\n')
                    sh.write('  exit 1\n')
                    sh.write('fi\n')
                    sh.write('if [ "$RUN_HLS" -eq 1 ]; then update_status hls attempted; fi\n')
                    sh.write('if [ "$RUN_HLX" -eq 1 ]; then update_status hlx attempted; fi\n')
                    sh.write('echo "Running Vivado HLS/HLX"\n')
                    sh.write('HW_START_TIME=$(date +%s)\n')
                    sh.write('run_status=0\n')
                    sh.write('if [ "$DRY_RUN" -eq 1 ]; then\n')
                    sh.write('  echo "DRY-RUN: (cd $HW_GEN_DIR/$ACC_TAG && BITSTREAM_SUFFIX=$BITSTREAM_SUFFIX $RUN_HW $RUN_HLS $RUN_HLX $OFFLOAD_HLS_HLX $COPY_BITS)"\n')
                    sh.write('else\n')
                    sh.write('  set +e\n')
                    sh.write('  (cd "$HW_GEN_DIR/$ACC_TAG" && BITSTREAM_SUFFIX="$BITSTREAM_SUFFIX" "$RUN_HW" "$RUN_HLS" "$RUN_HLX" "$OFFLOAD_HLS_HLX" "$COPY_BITS")\n')
                    sh.write('  run_status=$?\n')
                    sh.write('  set -e\n')
                    sh.write('fi\n')
                    sh.write(f'REPORT_DIR="$HW_GEN_DIR/$ACC_TAG/{settings["generated_files_dir"]}"\n')
                    sh.write(f'for report in {" ".join(settings["hlx_reports"])}; do\n')
                    sh.write('  if [ -f "$REPORT_DIR/$report" ]; then\n')
                    sh.write('    if [ "$DRY_RUN" -eq 1 ]; then\n')
                    sh.write('      echo "DRY-RUN: cp $REPORT_DIR/$report $RESULTS_DIR/"\n')
                    sh.write('    else\n')
                    sh.write('      cp "$REPORT_DIR/$report" "$RESULTS_DIR/"\n')
                    sh.write('    fi\n')
                    sh.write('  fi\n')
                    sh.write('done\n')
                    _write_parse_hls_hlx_status(sh, 'HW_GEN_DIR', 'ACC_TAG')
                    sh.write('if [ "$run_status" -ne 0 ]; then\n')
                    sh.write('  echo "Vivado HLS/HLX failed (status $run_status); copying logs"\n')
                    sh.write(f'  for log in {" ".join(failure_logs)}; do\n')
                    sh.write('    if [ -f "$HW_GEN_DIR/$ACC_TAG/$log" ]; then\n')
                    sh.write('      if [ "$DRY_RUN" -eq 1 ]; then\n')
                    sh.write('        echo "DRY-RUN: cp $HW_GEN_DIR/$ACC_TAG/$log $RESULTS_DIR/"\n')
                    sh.write('      else\n')
                    sh.write('        cp "$HW_GEN_DIR/$ACC_TAG/$log" "$RESULTS_DIR/"\n')
                    sh.write('      fi\n')
                    sh.write('    fi\n')
                    sh.write('  done\n')
                    sh.write('  exit "$run_status"\n')
                    sh.write('fi\n')
                    sh.write(f'for log in {" ".join(settings["hw_gen_logs"])}; do\n')
                    sh.write('  if [ -f "$HW_GEN_DIR/$ACC_TAG/$log" ]; then\n')
                    sh.write('    if [ "$DRY_RUN" -eq 1 ]; then\n')
                    sh.write('      echo "DRY-RUN: cp $HW_GEN_DIR/$ACC_TAG/$log $RESULTS_DIR/"\n')
                    sh.write('    else\n')
                    sh.write('      cp "$HW_GEN_DIR/$ACC_TAG/$log" "$RESULTS_DIR/"\n')
                    sh.write('    fi\n')
                    sh.write('  fi\n')
                    sh.write('done\n')
                    sh.write('if [ -n "$BITSTREAM_SRC" ]; then\n')
                    sh.write('  mkdir -p "$(dirname "$BITSTREAM_SRC")"\n')
                    sh.write('  if [ -f "$REPORT_DIR/$ACC_TAG.bit" ]; then\n')
                    sh.write('    if [ "$DRY_RUN" -eq 1 ]; then\n')
                    sh.write('      echo "DRY-RUN: cp $REPORT_DIR/$ACC_TAG.bit $BITSTREAM_SRC"\n')
                    sh.write('    else\n')
                    sh.write('      cp "$REPORT_DIR/$ACC_TAG.bit" "$BITSTREAM_SRC"\n')
                    sh.write('    fi\n')
                    sh.write('  fi\n')
                    sh.write('fi\n')
                    sh.write('if [ -n "$HWH_SRC" ]; then\n')
                    sh.write('  mkdir -p "$(dirname "$HWH_SRC")"\n')
                    sh.write('  if [ -f "$REPORT_DIR/$ACC_TAG.hwh" ]; then\n')
                    sh.write('    if [ "$DRY_RUN" -eq 1 ]; then\n')
                    sh.write('      echo "DRY-RUN: cp $REPORT_DIR/$ACC_TAG.hwh $HWH_SRC"\n')
                    sh.write('    else\n')
                    sh.write('      cp "$REPORT_DIR/$ACC_TAG.hwh" "$HWH_SRC"\n')
                    sh.write('    fi\n')
                    sh.write('  fi\n')
                    sh.write('fi\n')
                    sh.write('echo "Hardware generation completed. Use the run script to execute remote runs."\n')
                    sh.write('HW_END_TIME=$(date +%s)\n')
                    sh.write('HW_DURATION=$((HW_END_TIME - HW_START_TIME))\n')

                with run_remote_sh.open('w') as sh:
                    sh.write("#!/usr/bin/env bash\n")
                    sh.write("set -euo pipefail\n\n")
                    sh.write('DRY_RUN="${DRY_RUN:-0}"\n')
                    sh.write('RUN_ID="' + run_id + '"\n')
                    sh.write('export RUN_ID\n')
                    sh.write('RUN_DIR="${RUN_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"\n')
                    sh.write(f'RESULTS_DIR="$RUN_DIR/{settings["results_dir_name"]}"\n')
                    sh.write('REPO_ROOT="$RUN_DIR"\n')
                    sh.write(f'while [ ! -f "$REPO_ROOT/{settings["repo_root_marker"]}" ] && [ "$REPO_ROOT" != "/" ]; do\n')
                    sh.write('  REPO_ROOT="$(dirname "$REPO_ROOT")"\n')
                    sh.write('done\n')
                    sh.write(f'if [ ! -f "$REPO_ROOT/{settings["repo_root_marker"]}" ]; then\n')
                    sh.write(f'  echo "{settings["repo_root_marker"]} not found; cannot locate repo root"\n')
                    sh.write('  exit 1\n')
                    sh.write('fi\n\n')
                    _write_update_status_function(sh, settings["status_filename"])
                    sh.write('BOARD_INFO=$(REPO_ROOT="$REPO_ROOT" python3 - <<\'PY\'\n')
                    sh.write('import json, os, shlex\n')
                    sh.write('from pathlib import Path\n')
                    sh.write('repo_root = Path(os.environ.get("REPO_ROOT", ".")).resolve()\n')
                    sh.write('run_dir = Path(os.environ.get("RUN_DIR", ".")).resolve()\n')
                    sh.write(f'config_path = repo_root / "{settings["config_json"]}"\n')
                    sh.write(f'data = json.load(open("{settings["hw_params_filename"]}"))\n')
                    sh.write('hg = data.get("hardware_gen") or {}\n')
                    sh.write('cfg = json.load(config_path.open())\n')
                    sh.write('boards = cfg.get("boards", {})\n')
                    sh.write('board_name = hg.get("board") or (list(boards.keys())[0] if boards else "")\n')
                    sh.write('b = boards.get(board_name, {})\n')
                    sh.write(f'suffix_fmt = {json.dumps(settings["artifact_suffix_format"])}\n')
                    sh.write('run_id = os.environ.get("RUN_ID", "")\n')
                    sh.write('suffix = suffix_fmt.format(run_id=run_id) if run_id else ""\n')
                    sh.write('exp_name = hg.get("del")\n')
                    sh.write('exp_version = f"v{hg.get(\'del_version\')}" if hg.get("del_version") is not None else ""\n')
                    sh.write('acc_name = hg.get("acc_name")\n')
                    sh.write('if run_id and acc_name:\n')
                    sh.write('    acc_name = f"{acc_name}_{run_id}"\n')
                    sh.write('acc_version = hg.get("acc_version")\n')
                    sh.write('acc_sub = hg.get("acc_sub_version")\n')
                    sh.write('acc_bit = f"{acc_name}_{acc_version}_{acc_sub}{suffix}.bit"\n')
                    sh.write('acc_hwh = f"{acc_name}_{acc_version}_{acc_sub}{suffix}.hwh"\n')
                    sh.write('bin_name = f"{exp_name}_{exp_version}{suffix}"\n')
                    sh.write('try:\n')
                    sh.write('    rel_path = run_dir.relative_to(repo_root)\n')
                    sh.write('    path_to_exp = rel_path.as_posix()\n')
                    sh.write('except ValueError:\n')
                    sh.write('    path_to_exp = f"experiments/{exp_name}/{exp_version}"\n')
                    sh.write('bazel_target = f"//{path_to_exp}:exp"\n')
                    sh.write('bin_path = f"{repo_root}/bazel-bin/{path_to_exp}/exp"\n')
                    sh.write('bitstream_dir = cfg.get("bitstream_dir") or str(repo_root / "hardware_automation" / "bitstreams")\n')
                    sh.write('bit_src = f"{bitstream_dir}/{board_name}/{acc_bit}"\n')
                    sh.write('hwh_src = f"{bitstream_dir}/{board_name}/{acc_hwh}"\n')
                    sh.write('data_dir = cfg.get("data_dir", "")\n')
                    sh.write('models_dirs = cfg.get("models_dirs", [])\n')
                    sh.write('lines = {\n')
                    sh.write('  "BOARD_NAME": board_name,\n')
                    sh.write('  "BOARD_USER": b.get("board_user", ""),\n')
                    sh.write('  "BOARD_HOSTNAME": b.get("board_hostname", ""),\n')
                    sh.write('  "BOARD_PORT": str(b.get("board_port", "")),\n')
                    sh.write('  "BOARD_DIR": b.get("board_dir", ""),\n')
                    sh.write('  "EXP_NAME": exp_name or "",\n')
                    sh.write('  "EXP_VERSION": exp_version,\n')
                    sh.write('  "BIN_NAME": bin_name,\n')
                    sh.write('  "BAZEL_TARGET": bazel_target,\n')
                    sh.write('  "BIN_PATH": bin_path,\n')
                    sh.write('  "ACC_BIT": acc_bit,\n')
                    sh.write('  "ACC_HWH": acc_hwh,\n')
                    sh.write('  "BITSTREAM_SRC": bit_src,\n')
                    sh.write('  "HWH_SRC": hwh_src,\n')
                    sh.write('  "DATA_DIR": data_dir,\n')
                    sh.write('  "MODELS_DIRS": ":".join(models_dirs),\n')
                    sh.write('}\n')
                    sh.write('for k, v in lines.items():\n')
                    sh.write('    print(f"{k}={shlex.quote(str(v))}")\n')
                    sh.write('PY\n')
                    sh.write(')\n')
                    sh.write('eval "$BOARD_INFO"\n')
                    sh.write('if [ -z "$BOARD_NAME" ] || [ -z "$BOARD_USER" ] || [ -z "$BOARD_HOSTNAME" ] || [ -z "$BOARD_DIR" ]; then\n')
                    sh.write('  echo "Board configuration missing in config.json"\n')
                    sh.write('  exit 1\n')
                    sh.write('fi\n\n')
                    sh.write('mkdir -p "$RESULTS_DIR"\n\n')
                    sh.write('EXP_KEY="$EXP_NAME"\n')
                    sh.write('if [ -n "$EXP_VERSION" ]; then EXP_KEY="${EXP_KEY}_${EXP_VERSION}"; fi\n')
                    sh.write('REMOTE_EXP_DIR="$BOARD_DIR/experiments/$EXP_KEY"\n\n')
                    sh.write('remote_exec() {\n')
                    sh.write('  local cmd="$*"\n')
                    sh.write('  if [ "$DRY_RUN" -eq 1 ]; then\n')
                    sh.write('    echo "DRY-RUN: ssh -p $BOARD_PORT $BOARD_USER@$BOARD_HOSTNAME $cmd"\n')
                    sh.write('  else\n')
                    sh.write('    ssh -t -q -p "$BOARD_PORT" "$BOARD_USER"@"$BOARD_HOSTNAME" "bash -i -c \\\"$cmd\\\""\n')
                    sh.write('  fi\n')
                    sh.write('}\n')
                    sh.write('remote_rsync() {\n')
                    sh.write('  if [ "$DRY_RUN" -eq 1 ]; then\n')
                    sh.write('    echo "DRY-RUN: rsync -e ssh -p $BOARD_PORT $1 $BOARD_USER@$BOARD_HOSTNAME:$2"\n')
                    sh.write('  else\n')
                    sh.write('    rsync -q -r -avz -e "ssh -p $BOARD_PORT" "$1" "$BOARD_USER@$BOARD_HOSTNAME:$2"\n')
                    sh.write('  fi\n')
                    sh.write('}\n\n')
                    sh.write('update_status bazel_build attempted\n')
                    sh.write('BAZEL_START=$(date +%s)\n')
                    sh.write('echo "Compiling $BAZEL_TARGET for $BOARD_NAME"\n')
                    sh.write('bazel_ok=1\n')
                    sh.write('if [ "$DRY_RUN" -eq 1 ]; then\n')
                    sh.write('  echo "DRY-RUN: bazel6 build $BAZEL_TARGET ..."\n')
                    sh.write('else\n')
                    sh.write('  set +e\n')
                    sh.write('  (\n')
                    sh.write('    cd "$REPO_ROOT"\n')
                    sh.write('    if [ "$BOARD_NAME" = "Z1" ]; then\n')
                    sh.write('      bazel6 build "$BAZEL_TARGET" --cxxopt=\'-DACC_PROFILE\' --spawn_strategy=standalone --platforms=//platform:armv7_linux --@secda_tools//:config=fpga\n')
                    sh.write('    elif [ "$BOARD_NAME" = "KRIA" ]; then\n')
                    sh.write('      bazel6 build "$BAZEL_TARGET" --cxxopt=\'-DACC_PROFILE\' --spawn_strategy=standalone --platforms=//platform:aarch64_linux --@secda_tools//:config=fpga_arm64 --copt=\'-DKRIA\'\n')
                    sh.write('    else\n')
                    sh.write('      bazel6 build "$BAZEL_TARGET" --cxxopt=\'-DACC_PROFILE\' --spawn_strategy=standalone\n')
                    sh.write('    fi\n')
                    sh.write('  )\n')
                    sh.write('  bazel_ok=$?\n')
                    sh.write('  set -e\n')
                    sh.write('fi\n')
                    sh.write('BAZEL_END=$(date +%s)\n')
                    sh.write('BAZEL_DUR=$((BAZEL_END - BAZEL_START))\n')
                    sh.write('update_status bazel_build duration "$BAZEL_DUR"\n')
                    sh.write('if [ "$bazel_ok" -ne 0 ] || [ ! -f "$BIN_PATH" ]; then\n')
                    sh.write('  update_status bazel_build failure "${bazel_ok}"\n')
                    sh.write('  echo "Binary not found or build failed: $BIN_PATH"\n')
                    sh.write('  exit 1\n')
                    sh.write('fi\n')
                    sh.write('update_status bazel_build success 0\n\n')
                    sh.write('echo "Preparing remote directories"\n')
                    sh.write('remote_exec "mkdir -p $REMOTE_EXP_DIR/bins $REMOTE_EXP_DIR/bitstreams $REMOTE_EXP_DIR/exp $REMOTE_EXP_DIR/data $REMOTE_EXP_DIR/models"\n')
                    sh.write(f'remote_rsync "$REPO_ROOT/{settings["load_bitstream_script"]}" "$REMOTE_EXP_DIR/"\n')
                    sh.write('remote_rsync "$BIN_PATH" "$REMOTE_EXP_DIR/bins/$BIN_NAME"\n')
                    sh.write('remote_exec "chmod +x $REMOTE_EXP_DIR/bins/$BIN_NAME"\n')
                    sh.write('if [ -f "$BITSTREAM_SRC" ]; then\n')
                    sh.write('  remote_rsync "$BITSTREAM_SRC" "$REMOTE_EXP_DIR/bitstreams/$ACC_BIT"\n')
                    sh.write('fi\n')
                    sh.write('if [ -f "$HWH_SRC" ]; then\n')
                    sh.write('  remote_rsync "$HWH_SRC" "$REMOTE_EXP_DIR/bitstreams/$ACC_HWH"\n')
                    sh.write('fi\n\n')
                    sh.write('echo "Mapping bitstream $ACC_BIT"\n')
                    sh.write('if [ "$BOARD_NAME" = "KRIA" ]; then\n')
                    sh.write('  remote_exec "cd $REMOTE_EXP_DIR/bitstreams && python3 $REMOTE_EXP_DIR/load_bitstream.py $ACC_BIT"\n')
                    sh.write('else\n')
                    sh.write('  remote_exec "cd $REMOTE_EXP_DIR/bitstreams && sudo python3 $REMOTE_EXP_DIR/load_bitstream.py $ACC_BIT"\n')
                    sh.write('fi\n')
                    sh.write('update_status remote_run attempted\n')
                    sh.write('REMOTE_START=$(date +%s)\n')
                    sh.write(f'RUN_LOG="$RESULTS_DIR/{settings["run_log_name"].format(run_id="${RUN_ID}")}"\n')
                    sh.write('REMOTE_LOG="$REMOTE_EXP_DIR/exp/${BIN_NAME}.log"\n')
                    sh.write('LOCAL_REMOTE_LOG="$RESULTS_DIR/${BIN_NAME}.log"\n')
                    sh.write('rm -f "$RUN_LOG" "$LOCAL_REMOTE_LOG"\n')
                    sh.write('set +e\n')
                    sh.write('remote_exec "mkdir -p $REMOTE_EXP_DIR/exp && cd $REMOTE_EXP_DIR && sudo chmod +x ./bins/$BIN_NAME && sudo ./bins/$BIN_NAME 2>&1 | tee $REMOTE_LOG" | tee "$RUN_LOG"\n')
                    sh.write('remote_status=$?\n')
                    sh.write('set -e\n')
                    sh.write('echo "Summary: run_id=$RUN_ID board=$BOARD_NAME bin=$BIN_NAME" | tee -a "$RUN_LOG"\n')
                    sh.write('echo "Summary: log=$RUN_LOG" | tee -a "$RUN_LOG"\n')
                    sh.write('if [ "$DRY_RUN" -eq 1 ]; then\n')
                    sh.write('  echo "DRY-RUN: rsync -e ssh -p $BOARD_PORT $BOARD_USER@$BOARD_HOSTNAME:$REMOTE_LOG $LOCAL_REMOTE_LOG"\n')
                    sh.write('else\n')
                    sh.write('  rsync -q -avz -e "ssh -p $BOARD_PORT" "$BOARD_USER@$BOARD_HOSTNAME:$REMOTE_LOG" "$LOCAL_REMOTE_LOG"\n')
                    sh.write('fi\n')
                    sh.write('REMOTE_END=$(date +%s)\n')
                    sh.write('REMOTE_DUR=$((REMOTE_END - REMOTE_START))\n')
                    sh.write('update_status remote_run duration "$REMOTE_DUR"\n')
                    sh.write('if [ "$remote_status" -ne 0 ]; then\n')
                    sh.write('  update_status remote_run failure "$remote_status"\n')
                    sh.write('else\n')
                    sh.write('  update_status remote_run success 0\n')
                    sh.write('fi\n')
                run_sh.chmod(0o755)
                run_remote_sh.chmod(0o755)

                run_scripts.append(run_sh)

            writer.writerow({
                "run_id": run_id,
                "run_name": run_name,
                "params": json.dumps(mapping),
                "source_experiment": str(source_exp)
            })

    print(f"Wrote runs.csv -> {csv_path}")

    if not dry_run:
        build_repo_root = repo_root or find_repo_root(out_root, settings["repo_root_marker"])
        if build_repo_root:
            for run_dir in out_root.iterdir():
                if not run_dir.is_dir():
                    continue
                for source_rel in dict.fromkeys(source_rel_candidates):
                    try:
                        update_build_files(run_dir, build_repo_root, source_rel)
                    except ValueError:
                        continue

    global_hw = out_root / settings["hw_gen_all_name"]
    if dry_run:
        print(f"DRY-RUN: would create {global_hw}")
    else:
        with global_hw.open('w') as sh:
            sh.write("#!/usr/bin/env bash\n")
            sh.write("set -euo pipefail\n\n")
            sh.write('RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n')
            sh.write('echo "Running all hardware generation scripts under $RUN_DIR"\n')
            sh.write('ARGS=("$@")\n')
            sh.write('found=0\n')
            sh.write('failures=0\n')
            sh.write('failed_ids=()\n')
            sh.write(f'for script in "$RUN_DIR"/*/{settings["hw_gen_glob"]}; do\n')
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
            sh.write(f'  echo "No {settings["hw_gen_glob"]} scripts found under $RUN_DIR"\n')
            sh.write('  exit 1\n')
            sh.write('fi\n')
            sh.write('if [ "$failures" -ne 0 ]; then\n')
            sh.write('  echo "$failures script(s) failed"\n')
            sh.write('  echo "Failed run IDs: ${failed_ids[*]}"\n')
            sh.write('  exit 1\n')
            sh.write('fi\n')
        global_hw.chmod(0o755)

    global_run = out_root / settings["run_all_name"]
    if dry_run:
        print(f"DRY-RUN: would create {global_run}")
    else:
        with global_run.open('w') as sh:
            sh.write("#!/usr/bin/env bash\n")
            sh.write("set -euo pipefail\n\n")
            sh.write('RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n')
            sh.write('echo "Running all remote scripts under $RUN_DIR"\n')
            sh.write('ARGS=("$@")\n')
            sh.write('found=0\n')
            sh.write('failures=0\n')
            sh.write('failed_ids=()\n')
            sh.write(f'for script in "$RUN_DIR"/*/{settings["run_glob"]}; do\n')
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
            sh.write(f'  echo "No {settings["run_glob"]} scripts found under $RUN_DIR"\n')
            sh.write('  exit 1\n')
            sh.write('fi\n')
            sh.write('if [ "$failures" -ne 0 ]; then\n')
            sh.write('  echo "$failures script(s) failed"\n')
            sh.write('  echo "Failed run IDs: ${failed_ids[*]}"\n')
            sh.write('  exit 1\n')
            sh.write('fi\n')
        global_run.chmod(0o755)

    collect_results = out_root / settings["collect_results_name"]
    if dry_run:
        print(f"DRY-RUN: would create {collect_results}")
    else:
        with collect_results.open('w') as sh:
            sh.write("#!/usr/bin/env bash\n")
            sh.write("set -euo pipefail\n\n")
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
        collect_results.chmod(0o755)

    collect_dataset = out_root / settings["collect_dataset_name"]
    if dry_run:
        print(f"DRY-RUN: would create {collect_dataset}")
    else:
        with collect_dataset.open('w') as sh:
            sh.write("#!/usr/bin/env bash\n")
            sh.write("set -euo pipefail\n\n")
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
        collect_dataset.chmod(0o755)

    # print copy-pastable commands to run the generated "all" scripts
    cmd_hw = f'cd "{out_root}" && ./{settings["hw_gen_all_name"]}'
    cmd_run = f'cd "{out_root}" && ./{settings["run_all_name"]}'
    cmd_collect = f'cd "{out_root}" && ./{settings["collect_results_name"]}'
    cmd_dataset = f'cd "{out_root}" && ./{settings["collect_dataset_name"]}'
    print("Commands to run all hardware generation and remote runs:")
    print(cmd_hw)
    print(cmd_run)
    print(cmd_collect)
    print(cmd_dataset)
    print(f'{global_hw}')
    print(f'{global_run}')
    print(f'{collect_results}')
    print(f'{collect_dataset}')
    print("Done")


def main():

    # Remember the caller's working directory so relative CLI paths are resolved
    # against it, even after we os.chdir into the DSE_Explorer directory.
    orig_cwd = Path.cwd().resolve()

    abspath = os.path.abspath(__file__)
    dname = os.path.dirname(abspath)
    os.chdir(dname)
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument('--settings', default=None, help='Path to dse_setting.json (optional)')
    known, _ = pre.parse_known_args()

    default_settings_path = Path(__file__).resolve().parent / "dse_setting.json"
    settings_path = Path(known.settings) if known.settings else default_settings_path
    # Resolve --settings relative to original cwd if it was user-provided
    if known.settings:
        settings_path = (orig_cwd / settings_path).resolve()
    settings = load_settings(settings_path)

    p = argparse.ArgumentParser(parents=[pre])
    p.add_argument('--experiment', '-e', required=True,
                   help='Path to the experiment directory, e.g. experiments/mm_exp/v1')
    p.add_argument('--hw', '-j', default=None, help='Path to hw_params.json (optional)')
    p.add_argument('--output', '-o', default=settings["output_root"], help='Output directory for generated experiments')
    p.add_argument('--dry-run', action='store_true', help='Do not copy files; only print actions')
    p.add_argument('--sample', '-s', type=int, default=0, help='If >0, limit to this many runs (deterministic sampling)')
    args = p.parse_args()

    # Resolve user-provided relative paths against the original working directory
    source_exp = Path(args.experiment)
    if not source_exp.is_absolute():
        source_exp = (orig_cwd / source_exp).resolve()
    if not source_exp.exists():
        raise FileNotFoundError(f"Experiment path not found: {source_exp}")

    hw_params_name = settings["hw_params_filename"]
    hw_json = Path(args.hw) if args.hw else source_exp / hw_params_name
    if args.hw and not hw_json.is_absolute():
        hw_json = (orig_cwd / hw_json).resolve()
    if not hw_json.exists():
        # try recursive find
        found = list(source_exp.rglob(hw_params_name))
        if not found:
            raise FileNotFoundError(f"{hw_params_name} not found under {source_exp}")
        hw_json = found[0]

    exp_folder = format_experiment_folder(source_exp, settings["experiment_folder_format"])
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = (orig_cwd / out_path).resolve()
    out_root = (out_path / exp_folder).resolve()
    generate_runs(source_exp, hw_json, out_root, settings, dry_run=args.dry_run, sample=args.sample)
    if not args.dry_run:
        rewrite_build_deps_for_runs(out_root, source_exp, settings)


if __name__ == '__main__':
    main()
