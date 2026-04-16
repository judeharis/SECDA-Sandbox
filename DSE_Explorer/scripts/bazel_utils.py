from pathlib import Path
import re


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
