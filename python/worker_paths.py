from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def normalize_output_dir(value: Any) -> str:
    text = str(value or "")
    default = os.environ.get("PYMSS_STUDIO_DEFAULT_OUTPUT_DIR", "")
    output_path = Path(text or default or "results")
    if text and not output_path.is_absolute() and default:
        output_path = Path(default).parent / output_path
    return str(output_path.resolve())
