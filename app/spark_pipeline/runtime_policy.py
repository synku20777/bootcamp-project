from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

DEFAULT_ADVISORY_PARTITION_BYTES = 64 * 1024 * 1024
DEFAULT_BROADCAST_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_TARGET_FILE_BYTES = 128 * 1024 * 1024
DEFAULT_MIN_SHUFFLE_PARTITIONS = 4
DEFAULT_MAX_SHUFFLE_PARTITIONS = 200
DEFAULT_SKEW_RATIO_WARN = 5.0
DEFAULT_SKEW_SHARE_WARN = 0.10
DEFAULT_CLUSTER_MIN_OBSERVATIONS = 180
DEFAULT_CLUSTER_K_MIN = 2
DEFAULT_CLUSTER_K_MAX = 6


@dataclass(frozen=True, slots=True)
class SparkRuntimePolicy:
    """Measured thresholds used by the local Spark evidence workflow."""

    source_input_bytes: int
    advisory_partition_bytes: int
    shuffle_partitions: int
    shuffle_partitions_overridden: bool
    broadcast_max_bytes: int
    target_file_bytes: int
    skew_ratio_warn: float
    skew_share_warn: float
    cluster_min_observations: int
    cluster_k_min: int
    cluster_k_max: int
    min_shuffle_partitions: int = DEFAULT_MIN_SHUFFLE_PARTITIONS
    max_shuffle_partitions: int = DEFAULT_MAX_SHUFFLE_PARTITIONS

    @classmethod
    def from_manifest(
        cls,
        manifest: dict[str, Any],
        *,
        shuffle_partitions: int | None = None,
        advisory_partition_bytes: int = DEFAULT_ADVISORY_PARTITION_BYTES,
        broadcast_max_bytes: int = DEFAULT_BROADCAST_MAX_BYTES,
        target_file_bytes: int = DEFAULT_TARGET_FILE_BYTES,
        skew_ratio_warn: float = DEFAULT_SKEW_RATIO_WARN,
        skew_share_warn: float = DEFAULT_SKEW_SHARE_WARN,
        cluster_min_observations: int = DEFAULT_CLUSTER_MIN_OBSERVATIONS,
        cluster_k_min: int = DEFAULT_CLUSTER_K_MIN,
        cluster_k_max: int = DEFAULT_CLUSTER_K_MAX,
    ) -> SparkRuntimePolicy:
        files = manifest.get("files") or manifest.get("source_files") or {}
        source_input_bytes = sum(
            int(details.get("byte_count", 0))
            for details in files.values()
            if isinstance(details, dict)
        )
        _positive("advisory_partition_bytes", advisory_partition_bytes)
        _positive("broadcast_max_bytes", broadcast_max_bytes)
        _positive("target_file_bytes", target_file_bytes)
        _positive("cluster_min_observations", cluster_min_observations)
        if skew_ratio_warn <= 1:
            raise ValueError("skew_ratio_warn must be greater than one.")
        if not 0 < skew_share_warn <= 1:
            raise ValueError("skew_share_warn must be between zero and one.")
        if cluster_k_min < 2 or cluster_k_max < cluster_k_min:
            raise ValueError("Cluster k bounds must satisfy 2 <= minimum <= maximum.")

        derived_partitions = max(
            DEFAULT_MIN_SHUFFLE_PARTITIONS,
            min(
                DEFAULT_MAX_SHUFFLE_PARTITIONS,
                math.ceil(max(source_input_bytes, 1) / advisory_partition_bytes),
            ),
        )
        selected_partitions = (
            shuffle_partitions if shuffle_partitions is not None else derived_partitions
        )
        if not (
            DEFAULT_MIN_SHUFFLE_PARTITIONS
            <= selected_partitions
            <= DEFAULT_MAX_SHUFFLE_PARTITIONS
        ):
            raise ValueError(
                "shuffle_partitions must be between "
                f"{DEFAULT_MIN_SHUFFLE_PARTITIONS} and "
                f"{DEFAULT_MAX_SHUFFLE_PARTITIONS}."
            )
        return cls(
            source_input_bytes=source_input_bytes,
            advisory_partition_bytes=advisory_partition_bytes,
            shuffle_partitions=selected_partitions,
            shuffle_partitions_overridden=shuffle_partitions is not None,
            broadcast_max_bytes=broadcast_max_bytes,
            target_file_bytes=target_file_bytes,
            skew_ratio_warn=skew_ratio_warn,
            skew_share_warn=skew_share_warn,
            cluster_min_observations=cluster_min_observations,
            cluster_k_min=cluster_k_min,
            cluster_k_max=cluster_k_max,
        )

    def broadcast_decisions(self, manifest: dict[str, Any]) -> dict[str, bool]:
        files = manifest.get("files") or manifest.get("source_files") or {}
        return {
            name: bool(
                isinstance(files.get(name), dict)
                and 0
                <= int(files[name].get("byte_count", -1))
                <= self.broadcast_max_bytes
            )
            for name in ("mapping", "population", "indicators")
        }

    def target_file_count(self, input_bytes: int) -> int:
        return max(1, math.ceil(max(input_bytes, 1) / self.target_file_bytes))

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["shuffle_partition_derivation"] = (
            "explicit_override"
            if self.shuffle_partitions_overridden
            else "source_bytes_divided_by_advisory_bytes_with_bounds"
        )
        return payload


def _positive(name: str, value: int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive.")
