from __future__ import annotations

from dataclasses import dataclass

from pyspark.sql.types import (
    BooleanType,
    DateType,
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
)

CORRUPT_RECORD_COLUMN = "_corrupt_record"


@dataclass(frozen=True, slots=True)
class DatasetSchema:
    name: str
    filename: str
    headers: tuple[str, ...]
    schema: StructType
    required_columns: tuple[str, ...]
    numeric_columns: tuple[str, ...]
    date_columns: tuple[str, ...] = ()


def _schema(*fields: StructField) -> StructType:
    return StructType([*fields, StructField(CORRUPT_RECORD_COLUMN, StringType())])


ECDC = DatasetSchema(
    name="ecdc",
    filename="ecdc_global.csv",
    headers=("COUNTRY_REGION", "ISO3166_1", "REPORT_DATE", "CASES", "DEATHS"),
    schema=_schema(
        StructField("COUNTRY_REGION", StringType()),
        StructField("ISO3166_1", StringType()),
        StructField("REPORT_DATE", DateType()),
        StructField("CASES", DoubleType()),
        StructField("DEATHS", DoubleType()),
    ),
    required_columns=("COUNTRY_REGION", "REPORT_DATE"),
    numeric_columns=("CASES", "DEATHS"),
    date_columns=("REPORT_DATE",),
)

POPULATION = DatasetSchema(
    name="population",
    filename="population.csv",
    headers=(
        "COUNTRY_CODE_ISO2",
        "COUNTRY_CODE_ISO3",
        "COUNTRY_NAME",
        "POPULATION",
        "POPULATION_YEAR",
    ),
    schema=_schema(
        StructField("COUNTRY_CODE_ISO2", StringType()),
        StructField("COUNTRY_CODE_ISO3", StringType()),
        StructField("COUNTRY_NAME", StringType()),
        StructField("POPULATION", LongType()),
        StructField("POPULATION_YEAR", LongType()),
    ),
    required_columns=("COUNTRY_NAME", "POPULATION", "POPULATION_YEAR"),
    numeric_columns=("POPULATION", "POPULATION_YEAR"),
)

MAPPING = DatasetSchema(
    name="mapping",
    filename="country_mapping.csv",
    headers=(
        "SOURCE_COUNTRY_NAME",
        "SOURCE_COUNTRY_CODE",
        "NORMALIZED_COUNTRY_NAME",
        "NORMALIZED_ISO2",
        "NORMALIZED_ISO3",
        "EXPECTED_POPULATION_MATCH",
    ),
    schema=_schema(
        StructField("SOURCE_COUNTRY_NAME", StringType()),
        StructField("SOURCE_COUNTRY_CODE", StringType()),
        StructField("NORMALIZED_COUNTRY_NAME", StringType()),
        StructField("NORMALIZED_ISO2", StringType()),
        StructField("NORMALIZED_ISO3", StringType()),
        StructField("EXPECTED_POPULATION_MATCH", BooleanType()),
    ),
    required_columns=(
        "SOURCE_COUNTRY_NAME",
        "NORMALIZED_COUNTRY_NAME",
        "EXPECTED_POPULATION_MATCH",
    ),
    numeric_columns=(),
)

DATASETS = (ECDC, POPULATION, MAPPING)
