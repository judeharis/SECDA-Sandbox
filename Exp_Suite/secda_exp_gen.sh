#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Exp generation refactor: function-based, logging, error handling, CLI, board selection

DEFAULT_CONFIG="../config.json"
CONFIG_PATH="$DEFAULT_CONFIG"
VERBOSE=0
DRY_RUN=0

# copy bitstream to per-experiment folder when uploading
COPY_BITSTREAM=0
# automatically parse logs after collection
PARSE_RESULTS=0

# track experiments attempted during this script invocation (BOARD::EXP::VER)
RAN_EXPS=()
# operation flags
bin_gen=0
test_run=0
init_board=0
name=""

now() { date +"%Y_%m_%d_%H_%M"; }

log() {
  local level="$1"
  shift
  local msg="$*"
  local ts
  ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  if [ "$VERBOSE" -eq 1 ] || [ "$level" != "DEBUG" ]; then
    printf "%s [%s] %s\n" "$ts" "$level" "$msg"
  fi
}

log_debug() { log "DEBUG" "$*"; }
log_info() { log "INFO" "$*"; }
log_warn() { log "WARN" "$*"; }
log_error() { log "ERROR" "$*"; }

usage() {
  cat <<EOF
Usage: $0 [options]
Options:
  -h, --help           Show this help
  -c, --config <path>  Path to config.json (default: ${DEFAULT_CONFIG})
    --list-boards    List available boards from config.json
  -b, --build          Generate binaries (bazel build)
  -t, --test           Run test experiments on target board
  -i, --init           Initialize board directories and upload loader
  -s, --copy-bitstream  Copy the experiment's .bit into the per-experiment bitstreams/ folder on the board
  -p, --parse-results   After collection, run parse_logs.py on the results folder to generate performance_summary.csv
  -n, --name <name>    Experiment name prefix
  -v, --verbose        Verbose logging
      --dry-run        Don't execute remote commands (print only)
EOF
}

parse_args() {
  # simple getopt-based parsing for long options
  local ARGS
  ARGS=$(getopt -o hcbtin:svp --long help,config:,list-boards,build,test,init,name:,verbose,dry-run,copy-bitstream,parse-results -- "$@") || {
    usage
    exit 2
  }
  eval set -- "$ARGS"
  while true; do
    case "$1" in
    -h | --help)
      usage
      exit 0
      ;;
    -c | --config)
      CONFIG_PATH="$2"
      shift 2
      ;;
    --list-boards)
      list_boards
      exit 0
      ;;
    -b | --build)
      bin_gen=1
      shift
      ;;
    -t | --test)
      test_run=1
      shift
      ;;
    -i | --init)
      init_board=1
      shift
      ;;
    -n | --name)
      name="$2"
      shift 2
      ;;
    -v | --verbose)
      VERBOSE=1
      shift
      ;;
    -s | --copy-bitstream)
      COPY_BITSTREAM=1
      shift
      ;;
    -p | --parse-results)
      PARSE_RESULTS=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
    esac
  done
  if [ -z "${name:-}" ]; then
    name="run_$(now)"
  else
    name="${name}_$(now)"
  fi
}

load_global_config() {
  if [ ! -f "$CONFIG_PATH" ]; then
    log_error "Config file not found: $CONFIG_PATH"
    exit 1
  fi
  secda_init_path=$(jq -r '.secda_init_path // empty' "$CONFIG_PATH")
  # keep a copy of boards object
  BOARDS_JSON=$(jq -c '.boards' "$CONFIG_PATH")
}

list_boards() {
  if [ ! -f "$CONFIG_PATH" ]; then
    echo "Config not found: $CONFIG_PATH"
    exit 1
  fi
  echo "Available boards:"
  jq -r '.boards | keys[]' "$CONFIG_PATH"
}

get_board_config() {
  local board_key="$1"
  if [ -z "$board_key" ]; then
    log_error "No board key provided to get_board_config"
    return 1
  fi
  # Check board exists
  if ! echo "$BOARDS_JSON" | jq -e --arg k "$board_key" 'has($k)' >/dev/null; then
    log_error "Board '$board_key' not found in $CONFIG_PATH"
    return 2
  fi
  board_user=$(echo "$BOARDS_JSON" | jq -r --arg k "$board_key" '.[$k].board_user // empty')
  board_hostname=$(echo "$BOARDS_JSON" | jq -r --arg k "$board_key" '.[$k].board_hostname // empty')
  board_port=$(echo "$BOARDS_JSON" | jq -r --arg k "$board_key" '.[$k].board_port // empty')
  board_dir=$(echo "$BOARDS_JSON" | jq -r --arg k "$board_key" '.[$k].board_dir // empty')
  # other metadata available in boards JSON (kept out unless needed)

  # Fallbacks
  board_user=${board_user:-$(jq -r '.board_user // empty' "$CONFIG_PATH")}
  board_hostname=${board_hostname:-$(jq -r '.board_hostname // empty' "$CONFIG_PATH")}
  board_port=${board_port:-$(jq -r '.board_port // empty' "$CONFIG_PATH")}
  board_dir=${board_dir:-$(jq -r '.board_dir // empty' "$CONFIG_PATH")}

  log_debug "Selected board '$board_key' -> ${board_user}@${board_hostname}:${board_port} ${board_dir}"
}

remote_exec() {
  local cmd="$*"
  if [ "$DRY_RUN" -eq 1 ]; then
    log_info "DRY-RUN: ssh -p ${board_port} ${board_user}@${board_hostname} \"${cmd}\""
  else
    ssh -t -q -p "$board_port" "$board_user"@"$board_hostname" "bash -i -c '$cmd'"
  fi
}

# Run a remote command and return stdout (non-interactive). Empty string on DRY_RUN.
remote_sh() {
  local cmd="$*"
  if [ "$DRY_RUN" -eq 1 ]; then
    log_info "DRY-RUN: ssh -p ${board_port} ${board_user}@${board_hostname} \"${cmd}\""
    echo ""
  else
    ssh -q -p "$board_port" "$board_user"@"$board_hostname" "bash -lc '$cmd'"
  fi
}

remote_rsync() {
  local src="$1"
  local dest="$2"
  if [ "$DRY_RUN" -eq 1 ]; then
    log_info "DRY-RUN: rsync -e 'ssh -p ${board_port}' ${src} ${board_user}@${board_hostname}:${dest}"
  else
    rsync -q -r -avz -e "ssh -p ${board_port}" "$src" "${board_user}@${board_hostname}:${dest}"
  fi
}

init_directories() {
  log_info "Initializing directories on ${board_hostname}"
  # Create top-level directories and experiments container. Per-experiment subfolders
  # will be created when uploading builds or explicitly requested.
  remote_exec "mkdir -p ${board_dir} && mkdir -p ${board_dir}/bitstreams && mkdir -p ${board_dir}/experiments"
  # upload loader script
  remote_rsync "${SCRIPT_DIR}/scripts/load_bitstream.py" "${board_dir}/"
  log_info "Initialization done"
}

compile_and_upload() {
  local hw_array_len
  source "${SCRIPT_DIR}/configs.sh"
  BAZEL_EXP_ROOT="experiments"
  hw_array_len=${#hw_array[@]}
  for ((i = 0; i < hw_array_len; i++)); do
    hw=${hw_array[$i]}
    json_file="${REPO_ROOT}/hardware_automation/configs/${hw}.json"
    acc_name=$(jq -r '.acc_name' "$json_file")
    acc_version=$(jq -r '.acc_version' "$json_file")
    acc_sub_version=$(jq -r '.acc_sub_version' "$json_file")
    ACCNAME="${acc_name}_${acc_version}_${acc_sub_version}.bit"

    EXP_NAME=$(jq -r '.del' "$json_file")
    EXP_VERSION="v$(jq -r '.del_version' "$json_file")"
    BAZEL_EXP="//${BAZEL_EXP_ROOT}/${EXP_NAME}/${EXP_VERSION}:exp"
    PATH_TO_EXP="${BAZEL_EXP_ROOT}/${EXP_NAME}/${EXP_VERSION}"
    BIN_NAME="${EXP_NAME}_${EXP_VERSION}"

    # Determine which board this experiment targets (may be different per-hw)
    BOARD_NAME=$(jq -r '.board // empty' "$json_file")
    if [ -z "$BOARD_NAME" ]; then
      # fallback to the global board key
      BOARD_NAME="$BOARD_KEY"
    fi
    get_board_config "$BOARD_NAME"

    # If user requested init, ensure directories exist and upload loader to this board
    if [ "$init_board" -eq 1 ]; then
      log_info "Initializing directories on ${board_hostname} for experiment ${hw}"
      remote_exec "mkdir -p ${board_dir} && mkdir -p ${board_dir}/bitstreams && mkdir -p ${board_dir}/bins && mkdir -p ${board_dir}/models && mkdir -p ${board_dir}/data"
      remote_rsync "${SCRIPT_DIR}/scripts/load_bitstream.py" "${board_dir}/"
    fi

    if [ "$bin_gen" -eq 1 ]; then
      index=$((i + 1))
      log_info "Compiling: ${BIN_NAME} ${index}/${hw_array_len}"
      if [ "$DRY_RUN" -eq 1 ]; then
        log_info "DRY-RUN: bazel6 build ${BAZEL_EXP} ..."
      else

        # Build per-target board. Run bazel from the project root (secda_init_path)
        (
          cd "${secda_init_path}" || {
            log_error "Failed to cd to ${secda_init_path}"
            exit 1
          }
          case "$BOARD_NAME" in
          Z1)
            log_info "Building ${BAZEL_EXP} for Z1 (armv7)"
            bazel6 build "$BAZEL_EXP" \
              --cxxopt='-DACC_PROFILE' \
              --spawn_strategy=standalone \
              --platforms=//platform:armv7_linux \
              --@secda_tools//:config=fpga
            ;;
          KRIA)
            log_info "Building ${BAZEL_EXP} for KRIA (aarch64)"
            bazel6 build "$BAZEL_EXP" \
              --cxxopt='-DACC_PROFILE' \
              --spawn_strategy=standalone \
              --platforms=//platform:aarch64_linux \
              --@secda_tools//:config=fpga_arm64 \
              --copt='-DKRIA'
            ;;
          *)
            log_warn "Board '${BOARD_NAME}' not explicitly handled; using default build flags"
            bazel6 build "$BAZEL_EXP" \
              --cxxopt='-DACC_PROFILE' \
              --spawn_strategy=standalone
            ;;
          esac
        )

        # After build, ensure the binary exists before uploading
        SRC_BIN="${secda_init_path}/bazel-bin/${PATH_TO_EXP}/exp"
        if [ ! -f "$SRC_BIN" ]; then
          log_error "Build succeeded but binary not found at $SRC_BIN; skipping upload for ${BIN_NAME}"
        else
          # Create a per-experiment folder on the board and upload there.
          REMOTE_EXP_DIR="${board_dir}/experiments/${EXP_NAME}_${EXP_VERSION}"
          remote_exec "mkdir -p ${REMOTE_EXP_DIR}/bins ${REMOTE_EXP_DIR}/bitstreams"
          if [ -d "${secda_init_path}/${PATH_TO_EXP}/data" ]; then
            log_info "Found data folder for ${EXP_NAME}_${EXP_VERSION}; uploading to ${board_hostname}:${REMOTE_EXP_DIR}/data"
            remote_rsync "${secda_init_path}/${PATH_TO_EXP}/data/" "${REMOTE_EXP_DIR}/data/"
          fi
          remote_rsync "$SRC_BIN" "${REMOTE_EXP_DIR}/bins/${BIN_NAME}"
          remote_exec "cd ${REMOTE_EXP_DIR}/bins/ && chmod +x ./${BIN_NAME}"
          # Optionally copy the matching bitstream from host into the per-experiment bitstreams folder
          if [ "$COPY_BITSTREAM" -eq 1 ]; then
            # Board-specific host locations for the bitstream and corresponding hwh
            HOST_BIT="${REPO_ROOT}/hardware_automation/bitstreams/${BOARD_NAME}/${ACCNAME}"
            HOST_HWH="${REPO_ROOT}/hardware_automation/bitstreams/${BOARD_NAME}/${ACCNAME%.bit}.hwh"

            SRC_BIT=""
            SRC_HWH=""

            if [ -f "$HOST_BIT" ]; then
              SRC_BIT="$HOST_BIT"
            else
              log_warn "Bitstream ${ACCNAME} not found in ${HOST_BIT}; skipping bitstream copy"
            fi

            if [ -f "$HOST_HWH" ]; then
              SRC_HWH="$HOST_HWH"
            else
              log_warn "HWH ${HOST_HWH} not found in ${HOST_HWH}; skipping hwh copy"
            fi

            if [ -n "$SRC_BIT" ]; then
              log_info "Copying bitstream ${SRC_BIT} to ${REMOTE_EXP_DIR}/bitstreams/"
              remote_rsync "$SRC_BIT" "${REMOTE_EXP_DIR}/bitstreams/${ACCNAME}"
            fi

            if [ -n "$SRC_HWH" ]; then
              log_info "Copying hwh ${SRC_HWH} to ${REMOTE_EXP_DIR}/bitstreams/"
              remote_rsync "$SRC_HWH" "${REMOTE_EXP_DIR}/bitstreams/${ACCNAME%.bit}.hwh"
            fi
          fi
        fi
      fi
    fi
  done
}

copy_data_files() {
  log_info "Copying data files to each board in BOARD_KEYS"
  for bk in "${BOARD_KEYS[@]}"; do
    if ! get_board_config "$bk"; then
      log_warn "Skipping data copy for unknown board '$bk'"
      continue
    fi
    log_info "Copying data to ${board_hostname}:${board_dir}/data"
    remote_rsync "${secda_init_path}/data/" "${board_dir}/data/"
  done
}

run_experiments() {
  source "${SCRIPT_DIR}/configs.sh"
  ts=$(date +%Y_%m_%d_%H_%M_%S)
  RESULTS_DIR="${REPO_ROOT}/Exp_Suite/results/results_${ts}"
  PARSED_AT_END=0
  local hw_array_len=${#hw_array[@]}
  for ((i = 0; i < hw_array_len; i++)); do
    hw=${hw_array[$i]}
    json_file="${REPO_ROOT}/hardware_automation/configs/${hw}.json"
    acc_name=$(jq -r '.acc_name' "$json_file")
    acc_version=$(jq -r '.acc_version' "$json_file")
    acc_sub_version=$(jq -r '.acc_sub_version' "$json_file")
    ACCNAME="${acc_name}_${acc_version}_${acc_sub_version}.bit"
    EXP_NAME=$(jq -r '.del' "$json_file")
    EXP_VERSION="v$(jq -r '.del_version' "$json_file")"
    BIN_NAME="${EXP_NAME}_${EXP_VERSION}"
    BOARD_NAME=$(jq -r '.board // empty' "$json_file")
    if [ -z "$BOARD_NAME" ]; then
      BOARD_NAME="$BOARD_KEY"
    fi
    # select per-experiment board
    get_board_config "$BOARD_NAME"

    # log_info "Clearing caches on ${BOARD_NAME}"
    # Use tee with sudo to avoid shell redirection permission issues
    # remote_exec "sync && echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null"

    if [ "$test_run" -eq 1 ]; then
      index=$((i + 1))
      log_info "Running: ${BIN_NAME} ${index}/${hw_array_len}"
      log_info "Mapping Bitstream ${ACCNAME}"
      REMOTE_EXP_DIR="${board_dir}/experiments/${EXP_NAME}_${EXP_VERSION}"
      # Record this experiment as attempted in this invocation (used by collect_results)
      RAN_EXPS+=("${BOARD_NAME}::${EXP_NAME}::${EXP_VERSION}")
      # Attempt to load the bitstream from the per-experiment folder, falling back to the top-level bitstreams.
      # If loading fails, create a skip log on the remote and skip running the experiment.
      set +e
      if [ "$BOARD_NAME" == "KRIA" ]; then
        log_info "Using KRIA Board Specific Bitstream Loader (per-experiment folder)"
        remote_exec "cd ${REMOTE_EXP_DIR}/bitstreams/ && python3 ~/load_bitstream.py ${ACCNAME} || (cd ${board_dir}/bitstreams/ && python3 ~/load_bitstream.py ${ACCNAME})"
        load_rc=$?
        log_info "Clearing caches on ${BOARD_NAME}"
        if [ "$load_rc" -eq 0 ]; then
          remote_exec "sync && echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null && sleep 3"
        fi
      else
        log_info "Using Standard Bitstream Loader (per-experiment folder)"
        remote_exec "cd ${REMOTE_EXP_DIR}/bitstreams/ && sudo python3 ~/load_bitstream.py ${ACCNAME} || (cd ${board_dir}/bitstreams/ && sudo python3 ~/load_bitstream.py ${ACCNAME})"
        load_rc=$?
        log_info "Clearing caches on ${BOARD_NAME}"
        if [ "$load_rc" -eq 0 ]; then
          remote_exec "sync && echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null && sleep 3"
        fi
      fi
      set -e

      if [ "$load_rc" -ne 0 ]; then
        log_warn "Bitstream loading failed for ${EXP_NAME}_${EXP_VERSION} on ${BOARD_NAME}; skipping experiment"
        # Ensure remote run_logs directory exists and write a skip marker
        remote_exec "mkdir -p ${REMOTE_EXP_DIR}/run_logs && echo 'BITSTREAM SKIPPED' > ${REMOTE_EXP_DIR}/run_logs/${BIN_NAME}_BITSTREAM_SKIPPED.log"
        continue
      fi

      log_info "Executing remote binary ${BIN_NAME} (from per-experiment bins)"
      log_info "Experiment Dir: ${REMOTE_EXP_DIR}"

      # remote_exec "mkdir -p \"${REMOTE_EXP_DIR}/run_logs\" && cd ${REMOTE_EXP_DIR} && pwd && sudo chmod +x ./bins/${BIN_NAME} && sudo ./bins/${BIN_NAME} 2>&1 | tee ./run_logs/${BIN_NAME}_\$(date +%Y_%m_%d_%H_%M).log"
      remote_exec "mkdir -p \"${REMOTE_EXP_DIR}/run_logs\" && cd ${REMOTE_EXP_DIR} && pwd && sudo chmod +x ./bins/${BIN_NAME} && sudo ./bins/${BIN_NAME} 2>&1 | tee ./run_logs/latest.log && cp ./run_logs/latest.log ./run_logs/${BIN_NAME}_\$(date +%Y_%m_%d_%H_%M).log"
      # Always collect results after each run into ./results/results_TIME/
      mkdir -p "${RESULTS_DIR}"
      TIMESTAMP="$(date +%Y_%m_%d_%H_%M_%S)"
      ACCNAMEXT="${ACCNAME%.bit}"
      rsync -q -avz -e "ssh -p ${board_port}" "${board_user}@${board_hostname}:${REMOTE_EXP_DIR}/run_logs/latest.log" "${RESULTS_DIR}/${BOARD_NAME}_${EXP_NAME}_${EXP_VERSION}_${ACCNAMEXT}_${TIMESTAMP}.log"
      log_info "Experiment Done"
    fi
  done

  # Optionally parse all collected logs into a single CSV at the end.
  if [ "${PARSE_RESULTS:-0}" -eq 1 ]; then
    if [ -d "${RESULTS_DIR}" ]; then
      if [ "$DRY_RUN" -eq 1 ]; then
        log_info "DRY-RUN: python3 ${SCRIPT_DIR}/parse_logs.py --dataset ${RESULTS_DIR} --output ${RESULTS_DIR}/performance_summary.csv"
      else
        log_info "Parsing collected logs into ${RESULTS_DIR}/performance_summary.csv"
        python3 "${SCRIPT_DIR}/parse_logs.py" --dataset "${RESULTS_DIR}" --output "${RESULTS_DIR}/performance_summary.csv"
      fi
    else
      log_warn "parse-results enabled but results dir not found: ${RESULTS_DIR}"
    fi
  fi
}

clear_bitstream() {
  log_info "Clearing bitstream on remote"
  remote_exec "cd /home/ubuntu/bitstreams/ && python3 ~/load_bitstream.py -q CPU_1_0.bit"
}

on_exit() {
  local rc=$?
  if [ $rc -ne 0 ]; then
    log_error "Script exited with status $rc"
  else
    log_info "Script completed successfully"
  fi
}

trap on_exit EXIT

# ---- Main flow ----
parse_args "$@"
load_global_config

# Select a default board key from config.json (prefer KRIA if present)
# Determine default/global board key (prefer KRIA if present)
DEFAULT_BOARD_KEY=$(echo "$BOARDS_JSON" | jq -r 'if has("KRIA") then "KRIA" else keys[0] end')

# Gather unique boards required by the experiments in configs.sh
BOARD_KEYS=()
declare -A _seen_boards=()
# load hw_array from configs.sh if present
if [ -f "${SCRIPT_DIR}/configs.sh" ]; then
  # shellcheck source=/dev/null
  source "${SCRIPT_DIR}/configs.sh"
fi

if [ "${hw_array+set}" = "set" ] && [ "${#hw_array[@]}" -gt 0 ]; then
  for hw in "${hw_array[@]}"; do
    json_file="${REPO_ROOT}/hardware_automation/configs/${hw}.json"
    if [ ! -f "$json_file" ]; then
      log_warn "Experiment config not found for '${hw}' -> ${json_file}; skipping"
      continue
    fi
    b=$(jq -r '.board // empty' "$json_file")
    if [ -z "$b" ]; then
      b="$DEFAULT_BOARD_KEY"
    fi
    if [ -z "${_seen_boards[$b]:-}" ]; then
      BOARD_KEYS+=("$b")
      _seen_boards[$b]=1
    fi
  done
else
  # fallback to default board if no experiments defined
  BOARD_KEYS+=("$DEFAULT_BOARD_KEY")
fi

# Set primary BOARD_KEY to the first board for backward compatibility/fallbacks
BOARD_KEY="${BOARD_KEYS[0]}"

# Log configuration summary including all boards involved
log_info "-----------------------------------------------------------"
log_info "-- SECDA-Sandbox --"
log_info "-----------------------------------------------------------"
log_info "Configurations"
log_info "--------------"
log_info "Boards in use: ${BOARD_KEYS[*]}"
log_info "Primary Board Key: ${BOARD_KEY}"
log_info "Bin Gen: ${bin_gen}"
log_info "Test Run: ${test_run}"
log_info "Name: ${name}"
log_info "-----------------------------------------------------------"

# For each board, resolve config and optionally initialize directories/upload loader
for bk in "${BOARD_KEYS[@]}"; do
  get_board_config "$bk"
  log_info "Board '${bk}' -> ${board_user}@${board_hostname}:${board_port}  Dir:${board_dir}"
  if [ "$init_board" -eq 1 ]; then
    log_info "Initializing directories on ${bk} (${board_hostname})"
    init_directories
  fi
done

if [ "$bin_gen" -eq 1 ]; then
  compile_and_upload
fi

# copy_data_files

if [ "$test_run" -eq 1 ]; then
  run_experiments
fi

for bk in "${BOARD_KEYS[@]}"; do
  if ! get_board_config "$bk"; then
    log_warn "Skipping clear_bitstream for unknown board '${bk}'"
    continue
  fi
  log_info "Clearing bitstream on ${board_hostname} for board ${bk}"

  # If init flag set, copy CPU bitstream and .hwh to the board's bitstreams folder
  if [ "${init_board:-0}" -eq 1 ]; then
    log_info "Uploading CPU bitstream and hwh to ${board_hostname}:${board_dir}/bitstreams/"

    HOST_BIT="${REPO_ROOT}/hardware_automation/bitstreams/${bk}/CPU_1_0.bit"
    HOST_HWH="${REPO_ROOT}/hardware_automation/bitstreams/${bk}/CPU_1_0.hwh"

    SRC_BIT=""
    SRC_HWH=""

    if [ -f "$HOST_BIT" ]; then
      SRC_BIT="$HOST_BIT"
    else
      log_warn "CPU bitstream ${HOST_BIT} not found on host; skipping upload"
    fi

    if [ -f "$HOST_HWH" ]; then
      SRC_HWH="$HOST_HWH"
    else
      log_warn "CPU hwh ${HOST_HWH} not found on host; skipping upload"
    fi

    if [ -n "$SRC_BIT" ]; then
      log_info "Copying ${SRC_BIT} -> ${board_hostname}:${board_dir}/bitstreams/CPU_1_0.bit"
      remote_rsync "$SRC_BIT" "${board_dir}/bitstreams/CPU_1_0.bit"
    fi
    if [ -n "$SRC_HWH" ]; then
      log_info "Copying ${SRC_HWH} -> ${board_hostname}:${board_dir}/bitstreams/CPU_1_0.hwh"
      remote_rsync "$SRC_HWH" "${board_dir}/bitstreams/CPU_1_0.hwh"
    fi
  fi

  if [ "$bk" = "KRIA" ]; then
    # KRIA: use user loader (no sudo)
    remote_exec "cd ${board_dir}/bitstreams/ && python3 ~/load_bitstream.py -q CPU_1_0.bit"
  else
    # Other boards: run loader with sudo to ensure permissions
    remote_exec "cd ${board_dir}/bitstreams/ && sudo python3 ~/load_bitstream.py -q CPU_1_0.bit"
  fi
done
