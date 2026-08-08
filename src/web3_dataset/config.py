from __future__ import annotations

import os
from pathlib import Path


DEFAULT_CREDENTIALS = Path.home() / ".config/web3-audit-dataset/credentials.env"


def load_credentials(path: Path = DEFAULT_CREDENTIALS) -> None:
    if not path.exists():
        return
    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            raise ValueError(f"invalid credential line {line_number} in {path}")
        os.environ.setdefault(key.strip(), value.strip())
