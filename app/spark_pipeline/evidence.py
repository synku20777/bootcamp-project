"""Deterministic evidence assembly and atomic report publication.

Evidence serialization is deliberately separated from Spark orchestration:
all functions operate on driver-side dictionaries, making report contracts
testable without a SparkSession.  Authoritative reports use atomic replacement
so readers never observe a truncated document; fixture runs remain isolated as
preview artifacts.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.spark_pipeline.benchmark import FINGERPRINT_PROTOCOL
from app.spark_pipeline.runtime_policy import SparkRuntimePolicy


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a deterministic JSON document where atomic replacement is unnecessary."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_document(payload), encoding="utf-8")


def _json_document(payload: dict[str, Any]) -> str:
    """Serialize evidence canonically for stable documents and checksums."""

    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _document_sha256(payload: dict[str, Any]) -> str:
    """Return the checksum of the exact canonical JSON representation."""

    return hashlib.sha256(_json_document(payload).encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Replace a report only after its complete staged document is durable locally."""

    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.staging-{uuid4().hex}"
    try:
        staging.write_text(_json_document(payload), encoding="utf-8")
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def _conclusions(benchmarks: list[dict[str, Any]]) -> list[str]:
    """Convert benchmark medians into concise, data-backed statements."""

    conclusions = []
    for benchmark in benchmarks:
        before = benchmark["summary"]["before"]["duration_ms"]["median"]
        after = benchmark["summary"]["after"]["duration_ms"]["median"]
        direction = "faster" if after < before else "not faster"
        conclusions.append(
            f"{benchmark['name']}: optimized median was {direction} "
            f"({after} ms versus {before} ms)."
        )
    return conclusions


def _sanitized_evidence(
    *,
    environment: dict[str, Any],
    source_manifest: dict[str, Any],
    ingestion_id: str,
    benchmark_run_id: str,
    benchmarks: list[dict[str, Any]],
    layout: dict[str, Any],
    plans: dict[str, Any],
    quality: dict[str, Any],
    cold_end_to_end_ms: float,
    context_equivalence: dict[str, Any],
    runtime_policy: SparkRuntimePolicy,
    broadcast_decisions: dict[str, bool],
    skew: dict[str, Any],
    clustering: dict[str, Any],
    pipeline_task_metrics: dict[str, int],
) -> dict[str, Any]:
    """Assemble the versioned, shareable evidence contract.

    Full normalized plans are published separately as text because embedding
    them would make the JSON noisy and environment-sensitive.  Their compact
    metrics stay in this document for machine-readable audit evidence.
    """

    compact_plans = {
        name: {key: value for key, value in plan.items() if key != "normalized_plan"}
        for name, plan in plans.items()
    }
    return {
        "evidence_version": 4,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "fingerprint_protocol": FINGERPRINT_PROTOCOL,
        "environment": environment,
        "source_batch": {
            "source_batch_id": source_manifest["source_batch_id"],
            "batch_sha256": source_manifest["batch_sha256"],
            "snowflake_publication_generation_id": source_manifest.get(
                "snowflake_publication_generation_id"
            ),
            "files": {
                name: {
                    "row_count": details["row_count"],
                    "byte_count": details["byte_count"],
                    "sha256": details["sha256"],
                }
                for name, details in source_manifest["files"].items()
            },
            "world_bank_snapshot_id": source_manifest.get("world_bank_snapshot_id"),
        },
        "ingestion_id": ingestion_id,
        "benchmark_run_id": benchmark_run_id,
        "cold_end_to_end_ms": cold_end_to_end_ms,
        "quality": {
            "ruleset_version": quality["ruleset_version"],
            "status": quality["status"],
            "quality_document_sha256": quality["quality_document_sha256"],
        },
        "layout": layout,
        "runtime_policy": runtime_policy.as_dict(),
        "broadcast_decisions": broadcast_decisions,
        "country_key_skew": skew,
        "pipeline_task_metrics": pipeline_task_metrics,
        "plans": compact_plans,
        "country_context_equivalence": context_equivalence,
        "clustering": clustering,
        "benchmarks": benchmarks,
        "conclusions": _conclusions(benchmarks),
        "limitations": [
            "The workload is too small for Spark throughput benefits.",
            "Measurements are local-mode results and do not represent a cluster.",
            "Warm runs reduce startup and JIT noise but cannot remove filesystem-cache effects.",
            "Snowflake SQL remains the production semantic layer.",
            "Clustering segments historical reporting outcomes and is not causal.",
        ],
    }


def _publish_evidence_documents(
    *,
    source_kind: str | None,
    run_output: Path,
    evidence_path: Path,
    clustering_diagnostics_path: Path,
    evidence: dict[str, Any],
    clustering: dict[str, Any],
) -> None:
    """Publish authoritative evidence only for production Snowflake exports.

    Fixture data is useful for pipeline verification but must not overwrite
    production evidence.  Preview files make that distinction explicit while
    retaining identical payload schemas.
    """

    authoritative = source_kind == "snowflake_export"
    if authoritative:
        _write_json_atomic(clustering_diagnostics_path, clustering)
        _write_json_atomic(evidence_path, evidence)
        return
    _write_json_atomic(run_output / "clustering_diagnostics.preview.json", clustering)
    _write_json_atomic(run_output / "evidence.preview.json", evidence)
