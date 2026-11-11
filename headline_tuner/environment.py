"""Detects when the app is started outside its own virtual environment.

On machines with several Python installations, `python -m streamlit run app.py`
can silently pick up a global Streamlit / Anthropic SDK that is older than the
versions this app is built and tested with (requirements.txt).
"""

from __future__ import annotations

import re
from importlib import metadata

# package -> (minimum version, consequence if older)
MINIMUMS = {
    "anthropic": ((2, 8), "analysis requests will fail"),
    "streamlit": ((1, 50), "uploads and the interface may misbehave"),
}

START_COMMAND_WINDOWS = r".\run.ps1   (or .\.venv\Scripts\python.exe -m streamlit run app.py)"
START_COMMAND_UNIX = "./run.sh   (or .venv/bin/python -m streamlit run app.py)"


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text)[:3])


def environment_problems() -> list[str]:
    problems = []
    for package, (minimum, consequence) in MINIMUMS.items():
        try:
            installed = metadata.version(package)
        except metadata.PackageNotFoundError:
            problems.append(f"{package} is not installed")
            continue
        if _version_tuple(installed) < minimum:
            needed = ".".join(map(str, minimum))
            problems.append(f"{package} {installed} is older than {needed}, so {consequence}")
    return problems
