from pathlib import Path
import json


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
