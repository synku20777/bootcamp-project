from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.exceptions import DataSourceUnavailableError
from app.world_bank import WorldBankValidationError, load_snapshot


@lru_cache(maxsize=4)
def committed_snapshot_id(manifest_path: str) -> str:
    try:
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        snapshot_id = str(payload["snapshot_id"]).strip()
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        raise DataSourceUnavailableError(
            "World Bank context",
            code="context_data_unavailable",
            message="Country context data is temporarily unavailable.",
        ) from exc
    if not snapshot_id:
        raise DataSourceUnavailableError(
            "World Bank context",
            code="context_data_unavailable",
            message="Country context data is temporarily unavailable.",
        )
    return snapshot_id


@lru_cache(maxsize=4)
def committed_country_aliases(manifest_path: str) -> dict[str, str]:
    manifest = Path(manifest_path)
    suffix = ".manifest.json"
    if not manifest.name.endswith(suffix):
        raise DataSourceUnavailableError(
            "World Bank context",
            code="context_data_unavailable",
            message="Country context data is temporarily unavailable.",
        )
    snapshot = manifest.with_name(f"{manifest.name.removesuffix(suffix)}.csv")
    try:
        rows, _ = load_snapshot(snapshot, manifest)
    except WorldBankValidationError as exc:
        raise DataSourceUnavailableError(
            "World Bank context",
            code="context_data_unavailable",
            message="Country context data is temporarily unavailable.",
        ) from exc
    aliases: dict[str, str] = {}
    for row in rows:
        iso3 = str(row["CANONICAL_ISO3"]).upper()
        for value in (
            row.get("CANONICAL_ISO2"),
            row.get("CANONICAL_ISO3"),
            row.get("COUNTRY_NAME"),
        ):
            if value:
                aliases[str(value).strip().upper()] = iso3
    return aliases


def canonical_context_iso3(identifier: str, manifest_path: str) -> str:
    normalized = identifier.strip().upper()
    return committed_country_aliases(manifest_path).get(normalized, normalized)
