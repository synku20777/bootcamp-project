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
    def __init__(self, source: str) -> None:
        super().__init__(f"{source} is temporarily unavailable.")
        self.source = source


class CacheFillInProgressError(DataSourceUnavailableError):
    def __init__(self) -> None:
        super().__init__("Analytics cache")
