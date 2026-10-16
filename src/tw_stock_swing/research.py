from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol
from zoneinfo import ZoneInfo

from .analysis import analyze_bars
from .catalog import ResearchCatalog, load_catalog
from .coverage import CoverageEntry, complete_coverage, summarize_by_category
from .models import AnalysisResult
from .providers import YahooFinanceProvider
from .sources import ConnectorResult, NormalizedObservation, OFFICIAL_CONNECTORS


ACTIVE_CONNECTORS = {
    "TWSE": ("twse_market", "twse_institutional", "twse_margin"),
    "TPEx": ("tpex_market", "tpex_institutional", "tpex_margin"),
}


class ResearchConnector(Protocol):
    connector_id: str
    source_id: str
    endpoint: str

    def fetch_safe(self, symbol: str, *, timeout: float = 20.0) -> ConnectorResult: ...


@dataclass(frozen=True, slots=True)
class SourceHealth:
    connector_id: str
    source_id: str
    endpoint: str
    status: str
    fetched_at: datetime
    observations: int
    missing_fields: tuple[str, ...] = ()
    valid_times: tuple[datetime, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "source_id": self.source_id,
            "endpoint": self.endpoint,
            "status": self.status,
            "fetched_at": self.fetched_at.isoformat(),
            "observations": self.observations,
            "missing_fields": list(self.missing_fields),
            "valid_times": [value.isoformat() for value in self.valid_times],
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class CoverageItem:
    field_id: str
    name: str
    category: str
    priority: str
    implementation: str
    status: str
    reason: str
    source_id: str | None
    observed_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "name": self.name,
            "category": self.category,
            "priority": self.priority,
            "implementation": self.implementation,
            "status": self.status,
            "reason": self.reason,
            "source_id": self.source_id,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
        }


@dataclass(frozen=True, slots=True)
class ResearchReport:
    status: str
    generated_at: datetime
    symbol: str
    name: str
    market: str
    analysis: AnalysisResult
    official_observations: tuple[NormalizedObservation, ...]
    official_findings: tuple[str, ...]
    source_health: tuple[SourceHealth, ...]
    coverage: tuple[CoverageItem, ...]
    coverage_by_category: dict[str, dict[str, int]]
    warnings: tuple[str, ...]

    @property
    def observations_by_id(self) -> dict[str, NormalizedObservation]:
        return {item.field_id: item for item in self.official_observations}

    @property
    def missing_p0_categories(self) -> tuple[CoverageItem, ...]:
        passing = {"available", "computed", "not_applicable"}
        return tuple(
            item
            for item in self.coverage
            if item.field_id.startswith("category.")
            and item.priority == "P0"
            and item.status not in passing
        )

    def to_dict(self) -> dict[str, Any]:
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for observation in self.official_observations:
            category = observation.field_id.split(".", 1)[0]
            grouped.setdefault(category, {})[observation.field_id] = {
                "value": observation.value,
                "unit": observation.unit,
                "valid_time": observation.valid_time.isoformat(),
                "source_id": observation.source_id,
                "source_record_key": observation.source_record_key,
            }

        status_counts: dict[str, int] = {}
        for item in self.coverage:
            status_counts[item.status] = status_counts.get(item.status, 0) + 1

        return {
            "scope": "market_technical_institutional_margin",
            "generated_at": self.generated_at.isoformat(),
            "symbol": self.symbol,
            "name": self.name,
            "market": self.market,
            "analysis": self.analysis.to_dict(),
            "official": grouped,
            "official_findings": list(self.official_findings),
            "sources": [source.to_dict() for source in self.source_health],
            "coverage": {
                "summary": status_counts,
                "by_category": self.coverage_by_category,
                "missing_p0_categories": [item.to_dict() for item in self.missing_p0_categories],
                "items": [item.to_dict() for item in self.coverage],
            },
            "warnings": list(self.warnings),
            "disclaimer": "僅供研究與決策輔助；不是即時報價、投資建議或獲利保證。",
        }


def _market_for_symbol(symbol: str) -> str:
    normalized = symbol.upper()
    if normalized.endswith(".TWO"):
        return "TPEx"
    if normalized.endswith(".TW"):
        return "TWSE"
    raise ValueError(f"資料源未回傳可辨識的台股市場代號: {symbol}")


def _health(result: ConnectorResult, generated_at: datetime) -> SourceHealth:
    missing = tuple(item.field_id for item in result.coverage if item.status != "available")
    valid_times = tuple(sorted({item.valid_time for item in result.observations}))
    stale = bool(
        valid_times
        and (
            generated_at.astimezone(ZoneInfo("Asia/Taipei")).date()
            - valid_times[-1].astimezone(ZoneInfo("Asia/Taipei")).date()
        ).days
        > 5
    )
    status = "failed" if result.error else "partial" if missing else "stale" if stale else "ok"
    return SourceHealth(
        connector_id=result.connector_id,
        source_id=result.source_id,
        endpoint=result.endpoint,
        status=status,
        fetched_at=result.fetched_at,
        observations=len(result.observations),
        missing_fields=missing,
        valid_times=valid_times,
        error=result.error,
    )


def _gate_entry(field_id: str, result: ConnectorResult) -> CoverageEntry:
    statuses = {item.status for item in result.coverage}
    if result.error or "failed" in statuses:
        status, reason = "failed", result.error or "official_fetch_failed"
    elif statuses == {"available"}:
        status, reason = "available", "implemented_subset_available"
    else:
        status, reason = "missing", "official_fields_incomplete"
    return CoverageEntry(field_id, status, reason, result.source_id, result.fetched_at)


def _coverage_items(
    catalog: ResearchCatalog,
    results: tuple[ConnectorResult, ...],
    generated_at: datetime,
) -> tuple[tuple[CoverageItem, ...], dict[str, dict[str, int]]]:
    market_result, institutional_result, margin_result = results
    supplied = [entry for result in results for entry in result.coverage]
    supplied.extend(
        (
            _gate_entry("category.identity", market_result),
            _gate_entry("category.market", market_result),
            CoverageEntry(
                "category.technical",
                "computed",
                "deterministic_daily_indicators",
                "computed",
                generated_at,
            ),
            _gate_entry("category.institutional", institutional_result),
            _gate_entry("category.margin", margin_result),
        )
    )
    completed = complete_coverage(catalog, supplied)
    definitions = catalog.by_id
    items = tuple(
        CoverageItem(
            field_id=entry.field_id,
            name=definitions[entry.field_id].name,
            category=definitions[entry.field_id].category,
            priority=definitions[entry.field_id].priority,
            implementation=definitions[entry.field_id].implementation,
            status=entry.status,
            reason=entry.reason,
            source_id=entry.source_id,
            observed_at=entry.observed_at,
        )
        for entry in completed
    )
    return items, summarize_by_category(catalog, completed)


def _official_findings(
    observations: tuple[NormalizedObservation, ...],
) -> tuple[str, ...]:
    values = {item.field_id: item.value for item in observations}
    findings: list[str] = []
    institutions = (
        ("institutional.foreign.net", "外資"),
        ("institutional.trust.net", "投信"),
        ("institutional.dealer.net", "自營商"),
    )
    for field_id, label in institutions:
        raw = values.get(field_id)
        if not isinstance(raw, (int, float)):
            continue
        direction = "買超" if raw > 0 else "賣超" if raw < 0 else "買賣超持平"
        amount = f" {abs(raw):,.0f} 股" if raw else ""
        findings.append(f"{label}單日{direction}{amount}")

    foreign = values.get("institutional.foreign.net")
    trust = values.get("institutional.trust.net")
    if isinstance(foreign, (int, float)) and isinstance(trust, (int, float)):
        if foreign > 0 and trust > 0:
            findings.append("外資與投信單日同向買超")
        elif foreign < 0 and trust < 0:
            findings.append("外資與投信單日同向賣超")

    balances = (
        ("margin.financing.balance", "margin.financing.previous_balance", "融資"),
        ("margin.short.balance", "margin.short.previous_balance", "融券"),
    )
    for current_id, previous_id, label in balances:
        current = values.get(current_id)
        previous = values.get(previous_id)
        if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
            continue
        change = current - previous
        direction = "增加" if change > 0 else "減少" if change < 0 else "持平"
        amount = f" {abs(change):,.0f} 張" if change else ""
        findings.append(f"{label}餘額較前日{direction}{amount}")
    return tuple(findings)


def research_stock(
    symbol: str,
    *,
    timeout: float = 20.0,
    provider: YahooFinanceProvider | None = None,
    connectors: Mapping[str, ResearchConnector] | None = None,
    catalog: ResearchCatalog | None = None,
    generated_at: datetime | None = None,
) -> ResearchReport:
    """Build the first usable single-stock report from live and computed data."""

    if timeout <= 0:
        raise ValueError("timeout 必須大於 0")
    when = generated_at or datetime.now(ZoneInfo("Asia/Taipei"))
    if when.tzinfo is None:
        raise ValueError("generated_at 必須包含時區")

    active_provider = provider if provider is not None else YahooFinanceProvider(timeout=timeout)
    market_data = active_provider.fetch(symbol)
    analysis = analyze_bars(market_data)
    market = _market_for_symbol(market_data.symbol)
    base_symbol = market_data.symbol.split(".", 1)[0]
    active_connectors = connectors if connectors is not None else OFFICIAL_CONNECTORS
    connector_ids = ACTIVE_CONNECTORS[market]
    selected = tuple(active_connectors[connector_id] for connector_id in connector_ids)

    with ThreadPoolExecutor(max_workers=len(selected)) as executor:
        futures = [
            executor.submit(connector.fetch_safe, base_symbol, timeout=timeout)
            for connector in selected
        ]
        results = tuple(future.result() for future in futures)

    observations = tuple(item for result in results for item in result.observations)
    official_name = next(
        (
            str(item.value)
            for item in observations
            if item.field_id == "identity.name" and str(item.value).strip()
        ),
        analysis.name,
    )
    official_health = tuple(_health(result, when) for result in results)
    yahoo_health = SourceHealth(
        connector_id="yahoo_daily",
        source_id="yahoo_finance",
        endpoint="Yahoo Finance Chart API",
        status="ok",
        fetched_at=when,
        observations=len(market_data.bars),
        valid_times=(),
    )

    active_catalog = catalog if catalog is not None else load_catalog()
    coverage, by_category = _coverage_items(active_catalog, results, when)
    warnings: list[str] = []
    if analysis.freshness_warning:
        warnings.append(analysis.freshness_warning)
    for source in official_health:
        if source.status == "failed":
            warnings.append(f"{source.connector_id} 失敗：{source.error}")
        elif source.status == "partial":
            warnings.append(
                f"{source.connector_id} 缺少欄位：{', '.join(source.missing_fields)}"
            )
        elif source.status == "stale":
            warnings.append(
                f"{source.connector_id} 最新資料為 {source.valid_times[-1].date().isoformat()}，可能已過期"
            )
    missing_p0_names = [
        item.name
        for item in coverage
        if item.field_id.startswith("category.")
        and item.priority == "P0"
        and item.status not in {"available", "computed", "not_applicable"}
    ]
    if missing_p0_names:
        warnings.append("尚未涵蓋的 P0 類別：" + "、".join(missing_p0_names))

    active_status = "ok"
    if analysis.freshness_warning or any(source.status != "ok" for source in official_health):
        active_status = "partial"
    return ResearchReport(
        status=active_status,
        generated_at=when,
        symbol=market_data.symbol,
        name=official_name,
        market=market,
        analysis=analysis,
        official_observations=observations,
        official_findings=_official_findings(observations),
        source_health=(yahoo_health, *official_health),
        coverage=coverage,
        coverage_by_category=by_category,
        warnings=tuple(warnings),
    )
