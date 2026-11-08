"""Read and write the .env file without losing what is already in it."""
from __future__ import annotations

import os
import re
import shutil

_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$")
PLACEHOLDER = re.compile(r"your-|-here\b", re.I)


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v.split(" #")[0].strip()


def read_env(path: str) -> dict[str, str]:
    """KEY -> value for every real setting (placeholders such as 'sk-your-key-here' count as empty)."""
    out: dict[str, str] = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = _LINE.match(line)
            if m:
                v = _unquote(m.group(2))
                out[m.group(1)] = "" if PLACEHOLDER.search(v) else v
    return out


def _fmt(value: str) -> str:
    if re.search(r"[\s#\"']", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def write_env(path: str, template: str, values: dict[str, str]) -> None:
    """Set ``values`` in the file. An empty file starts from the template (.env.example). Existing lines keep their order
    and comments; settings the file does not have yet are added at the end. The old file is kept as .env.bak."""
    for k, v in values.items():
        if "\n" in v or "\r" in v:
            raise ValueError(f"{k} must be a single line")
    lines: list[str] = []
    if os.path.exists(path) and os.path.getsize(path) > 0:
        shutil.copyfile(path, path + ".bak")
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    elif os.path.exists(template):
        with open(template, encoding="utf-8") as f:
            lines = f.read().splitlines()
    done: set[str] = set()
    out: list[str] = []
    for line in lines:
        m = _LINE.match(line)
        if m and m.group(1) in values:
            out.append(f"{m.group(1)}={_fmt(values[m.group(1)])}")
            done.add(m.group(1))
        elif m and PLACEHOLDER.search(_unquote(m.group(2))):
            out.append(f"{m.group(1)}=")      # a leftover example value is not a real setting
        else:
            out.append(line)
    extra = [f"{k}={_fmt(v)}" for k, v in values.items() if k not in done]
    if extra:
        if out and out[-1].strip():
            out.append("")
        out += extra
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip("\n") + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
