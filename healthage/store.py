"""Local storage. Everything lives in one folder on the user's machine
(default ~/.healthage, override with HEALTHAGE_HOME), files readable by the
owner only. Nothing is sent anywhere except the user's own Google API calls."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def home() -> Path:
    p = Path(os.environ.get("HEALTHAGE_HOME", "~/.healthage")).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    try:
        p.chmod(0o700)
    except OSError:
        pass
    return p


def read(name: str, default: Any = None) -> Any:
    f = home() / name
    if not f.exists():
        return default
    try:
        return json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def write(name: str, obj: Any) -> None:
    f = home() / name
    tmp = f.with_suffix(f.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(obj, fh, indent=1)
    os.replace(tmp, f)


def delete(name: str) -> None:
    f = home() / name
    if f.exists():
        f.unlink()
