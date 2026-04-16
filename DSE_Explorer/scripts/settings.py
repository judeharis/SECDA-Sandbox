from pathlib import Path
import json

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
    "parse_hardware_script",
    "parse_performance_script",
    "load_bitstream_script",
    "run_log_name",
    "sim_run_log_name",
    "hw_gen_script_template",
    "run_script_template",
    "sim_script_template",
    "hw_gen_glob",
    "run_glob",
    "sim_glob",
    "hw_gen_all_name",
    "run_all_name",
    "sim_all_name",
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
