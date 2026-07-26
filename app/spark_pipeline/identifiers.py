from __future__ import annotations

import argparse
import re

BATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def valid_batch_id(value: str) -> str:
    if not BATCH_ID_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "IDs may contain only letters, numbers, '.', '_' and '-'."
        )
    return value
