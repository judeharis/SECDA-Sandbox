from pathlib import Path
import re

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
