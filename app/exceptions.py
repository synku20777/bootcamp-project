from __future__ import annotations


class ApplicationError(Exception):
    """Base class for errors safe to translate at the HTTP boundary."""


class CountryNotFoundError(ApplicationError):
    def __init__(self, identifier: str) -> None:
        super().__init__("Country was not found.")
        self.identifier = identifier


class DomainValidationError(ApplicationError):
    """Raised when a syntactically valid request violates a domain rule."""


class InvalidMetricError(DomainValidationError):
    """Raised when a requested metric is not allowlisted."""


class DataSourceUnavailableError(ApplicationError):
    def __init__(
        self,
        source: str,
        *,
        code: str = "dependency_unavailable",
        message: str | None = None,
    ) -> None:
        super().__init__(message or f"{source} is temporarily unavailable.")
        self.source = source
        self.code = code


class CacheFillInProgressError(DataSourceUnavailableError):
    def __init__(self) -> None:
        super().__init__(
            "Analytics cache",
            code="cache_fill_in_progress",
            message="Analytics data is being prepared. Try again shortly.",
        )


def cache_unavailable_error() -> DataSourceUnavailableError:
    return DataSourceUnavailableError(
        "Redis cache",
        code="cache_unavailable",
        message="The analytics cache is temporarily unavailable.",
    )


def mongodb_unavailable_error() -> DataSourceUnavailableError:
    return DataSourceUnavailableError(
        "MongoDB",
        code="mongodb_unavailable",
        message="MongoDB is temporarily unavailable.",
    )
