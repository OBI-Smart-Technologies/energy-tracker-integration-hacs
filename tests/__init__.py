from __future__ import annotations

from pathlib import Path

import custom_components

custom_components.__path__ = [
    entry for entry in custom_components.__path__ if Path(entry).is_dir()
]
