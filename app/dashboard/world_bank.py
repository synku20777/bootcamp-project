from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

DisplayKind = Literal["population", "density", "percentage", "currency"]


@dataclass(frozen=True, slots=True)
class WorldBankMetricDefinition:
    key: str
    title: str
    year: int
    unit: str
    indicator_code: str
    display: DisplayKind
    icon: str
    note: str | None = None

    @property
    def selector_label(self) -> str:
        return f"{self.title}, {self.year}"

    @property
    def metadata(self) -> str:
        return f"{self.year} · {self.indicator_code} · {self.unit}"


# One registry keeps years, units, codes, and precision identical across Country
# Explorer and Comparison. Duplicating these presentation rules would let two
# dashboard pages assign different meanings to the same source observation.
WORLD_BANK_BASELINE_METRICS = (
    WorldBankMetricDefinition(
        key="population_2020_context",
        title="WDI population",
        year=2020,
        unit="people",
        indicator_code="SP.POP.TOTL",
        display="population",
        icon="tabler:users-group",
        note="Context only; not used as the COVID rate denominator.",
    ),
    WorldBankMetricDefinition(
        key="population_density_2019",
        title="Population density",
        year=2019,
        unit="people/km²",
        indicator_code="EN.POP.DNST",
        display="density",
        icon="tabler:map-pin",
    ),
    WorldBankMetricDefinition(
        key="population_age_65_plus_pct_2019",
        title="Population aged 65+",
        year=2019,
        unit="% of population",
        indicator_code="SP.POP.65UP.TO.ZS",
        display="percentage",
        icon="tabler:accessible",
    ),
    WorldBankMetricDefinition(
        key="real_gdp_per_capita_2019",
        title="Real GDP per capita",
        year=2019,
        unit="constant 2015 US$",
        indicator_code="NY.GDP.PCAP.KD",
        display="currency",
        icon="tabler:currency-dollar",
    ),
    WorldBankMetricDefinition(
        key="health_expenditure_per_capita_ppp_2019",
        title="Health expenditure per person, PPP",
        year=2019,
        unit="current international $",
        indicator_code="SH.XPD.CHEX.PP.CD",
        display="currency",
        icon="tabler:building-hospital",
    ),
)

WORLD_BANK_METRICS_BY_KEY = {
    definition.key: definition for definition in WORLD_BANK_BASELINE_METRICS
}
DEFAULT_WORLD_BANK_COMPARISON_METRIC = "population_density_2019"


def world_bank_metric(key: str | None) -> WorldBankMetricDefinition:
    return WORLD_BANK_METRICS_BY_KEY.get(
        key or DEFAULT_WORLD_BANK_COMPARISON_METRIC,
        WORLD_BANK_METRICS_BY_KEY[DEFAULT_WORLD_BANK_COMPARISON_METRIC],
    )


def indicator_is_available(indicator: dict[str, Any] | None) -> bool:
    return bool(
        indicator
        and indicator.get("status") == "available"
        and indicator.get("value") is not None
    )


def format_world_bank_indicator(
    indicator: dict[str, Any] | None,
    definition: WorldBankMetricDefinition,
) -> str:
    if not indicator_is_available(indicator):
        return "Not available"

    value = indicator["value"]
    if definition.display == "population":
        return f"{value:,.0f}"
    if definition.display == "density":
        return f"{value:,.1f}"
    if definition.display == "percentage":
        return f"{value:,.1f}%"
    return f"${value:,.0f}"
