"""Clustering-stage orchestration and model evidence enrichment.

Model fitting and feature transformations remain implemented in
``clustering``.  This adapter composes them with pipeline lineage, runtime
policy, skew evidence, and artifact publication so the main harness only owns
stage ordering and SparkSession lifetime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pyspark.sql import DataFrame, SparkSession

from app.spark_pipeline.clustering import (
    ClusteringResult,
    publish_clustering_artifacts,
    run_country_clustering,
)
from app.spark_pipeline.runtime_policy import SparkRuntimePolicy
from app.spark_pipeline.transformations import country_baseline


def _clustering_stage(
    spark: SparkSession,
    frames: dict[str, DataFrame],
    *,
    policy: SparkRuntimePolicy,
    broadcast_decisions: dict[str, bool],
    model_id: str,
    run_output: Path,
    source_manifest: dict[str, Any],
    skew: dict[str, Any],
) -> dict[str, Any]:
    """Fit, publish, and describe one clustering model run.

    ``ClusteringResult`` may retain cached Spark frames used during model
    selection.  Cleanup is therefore guaranteed here—the boundary that owns
    the result—even when artifact publication or diagnostics enrichment fails.
    """

    result: ClusteringResult | None = None
    try:
        source_files = source_manifest.get("files") or source_manifest.get(
            "source_files", {}
        )
        extended_source = source_files.get("covid_extended", {})
        result = run_country_clustering(
            spark,
            frames["covid_extended"],
            country_baseline(frames["indicators"]),
            policy=policy,
            model_id=model_id,
            broadcast_baseline=broadcast_decisions["indicators"],
        )
        result.diagnostics.update(
            {
                "source_lineage": {
                    "source_batch_id": source_manifest["source_batch_id"],
                    "source_batch_sha256": source_manifest.get(
                        "batch_sha256", source_manifest.get("source_batch_sha256")
                    ),
                    "source_kind": source_manifest.get("source_kind"),
                    "world_bank_snapshot_id": source_manifest.get(
                        "world_bank_snapshot_id"
                    ),
                    "extended_source": {
                        "snowflake_object": (
                            "COVID_ANALYTICS.MARTS.COVID_ENRICHED_EXTENDED"
                        ),
                        "row_count": extended_source.get("row_count"),
                        "minimum_report_date": extended_source.get(
                            "minimum_report_date"
                        ),
                        "maximum_report_date": extended_source.get(
                            "maximum_report_date"
                        ),
                        "country_count": extended_source.get("country_count"),
                        "byte_count": extended_source.get("byte_count"),
                        "sha256": extended_source.get("sha256"),
                    },
                },
                "runtime_policy": policy.as_dict(),
                "broadcast_decisions": broadcast_decisions,
                "country_key_skew": skew,
            }
        )
        output_metrics = publish_clustering_artifacts(
            result,
            target=run_output / "clustering" / f"model_id={model_id}",
        )
        result.diagnostics["local_publication"] = {
            "published": True,
            **output_metrics,
        }
        return result.diagnostics
    finally:
        if result is not None:
            result.cleanup()
