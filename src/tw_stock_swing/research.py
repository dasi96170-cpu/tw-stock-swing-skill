from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import math
from typing import Any, Mapping, Protocol
from zoneinfo import ZoneInfo

from .analysis import analyze_bars, expected_completed_session
from .catalog import ResearchCatalog, load_catalog
from .coverage import CoverageEntry, CoverageError, assert_strict_p0_coverage, complete_coverage, summarize_by_category
from .models import AnalysisResult
from .providers import DataProviderError, YahooFinanceProvider, symbol_candidates
from .sources import ConnectorError, ConnectorResult, NormalizedObservation, OFFICIAL_CONNECTORS
from .sources.company import CompanyResult, COMPANY_CONNECTORS, fetch_company_sources
from .storage import ResearchDatabase


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
    content_hash: str | None = None
    valid_time_verified: bool = False
    time_status: str = "unknown"
    completeness_status: str = "unknown"

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
            "content_hash": self.content_hash,
            "valid_time_verified": self.valid_time_verified,
            "time_status": self.time_status,
            "completeness_status": self.completeness_status,
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
    analysis: AnalysisResult | None
    official_observations: tuple[NormalizedObservation, ...]
    official_findings: tuple[str, ...]
    source_health: tuple[SourceHealth, ...]
    coverage: tuple[CoverageItem, ...]
    coverage_by_category: dict[str, dict[str, int]]
    decision_blockers: tuple[str, ...]
    market_conflicts: tuple[str, ...]
    warnings: tuple[str, ...]
    run_id: str | None = None
    database_path: str | None = None
    company_sources: tuple[CompanyResult, ...] = ()
    analysis_error: str | None = None
    market_resolution: str = "provider_symbol"
    routing_error: str | None = None

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
            and (item.implementation != "implemented" or item.status not in passing)
        )

    @property
    def decision_eligible(self) -> bool:
        return (self.status == "complete" and not self.decision_blockers
                and self.analysis is not None and self.analysis.screening_eligible
                and self.market in ACTIVE_CONNECTORS and not self.routing_error)

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

        analysis_payload = self.analysis.to_dict(decision_eligible=self.decision_eligible) if self.analysis else None
        if analysis_payload is not None and not self.decision_eligible:
            for field in (
                "entry_low", "entry_high", "stop_loss", "target_1", "target_2",
                "risk_reward_1",
            ):
                analysis_payload[field] = None

        return {
            "status": self.status,
            "decision_eligible": self.decision_eligible,
            "decision_blockers": list(self.decision_blockers),
            "scope": "market_technical_institutional_margin",
            "additional_scopes": ["company_profile", "official_announcement_metadata"],
            "company_sources": [item.to_dict() for item in self.company_sources],
            "generated_at": self.generated_at.isoformat(),
            "symbol": self.symbol,
            "name": self.name,
            "market": self.market,
            "analysis": analysis_payload,
            "analysis_error": self.analysis_error,
            "market_resolution": self.market_resolution,
            "routing_error": self.routing_error,
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
            "market_conflicts": list(self.market_conflicts),
            "persistence": {
                "run_id": self.run_id,
                "database": self.database_path,
                "saved": self.run_id is not None,
            },
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
    expected = expected_completed_session(generated_at)
    dates = [value.astimezone(ZoneInfo("Asia/Taipei")).date() for value in valid_times]
    stale = any(value < expected for value in dates)
    future = any(value > expected for value in dates)
    time_status = ("unverified_time" if not result.valid_time_verified or not dates else
                   "future" if future else "stale" if stale else "ok")
    completeness = "partial" if missing else "complete"
    status = (
        "failed" if result.error else
        time_status if time_status != "ok" else
        "partial" if missing else
        "ok"
    )
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
        content_hash=result.content_hash,
        valid_time_verified=result.valid_time_verified,
        time_status=time_status,
        completeness_status=completeness,
    )


def _gate_entry(
    field_id: str,
    result: ConnectorResult,
    evaluated_coverage: tuple[CoverageEntry, ...],
) -> CoverageEntry:
    statuses = {item.status for item in evaluated_coverage}
    if result.error or "failed" in statuses:
        status, reason = "failed", result.error or "official_fetch_failed"
    elif "stale" in statuses:
        status, reason = "stale", "official_source_stale"
    elif "unverified" in statuses:
        status, reason = "unverified", "official_source_time_unverified"
    elif "conflicting" in statuses:
        status, reason = "conflicting", "official_source_conflict"
    elif statuses == {"available"}:
        status, reason = "available", "implemented_subset_available"
    else:
        status, reason = "missing", "official_fields_incomplete"
    return CoverageEntry(field_id, status, reason, result.source_id, result.fetched_at)


def _coverage_items(
    catalog: ResearchCatalog,
    results: tuple[ConnectorResult, ...],
    generated_at: datetime,
    company_results: tuple[CompanyResult, ...] = (),
    technical_error: str | None = None,
) -> tuple[tuple[CoverageItem, ...], dict[str, dict[str, int]]]:
    market_result, institutional_result, margin_result = results
    evaluated: list[tuple[CoverageEntry, ...]] = []
    for result in results:
        observations = {item.field_id: item for item in result.observations}
        def evaluate(entry: CoverageEntry) -> CoverageEntry:
            if entry.status != "available":
                return entry
            observation = observations.get(entry.field_id)
            if not result.valid_time_verified or observation is None:
                return replace(entry, status="unverified", reason="official_source_time_unverified")
            valid_date = observation.valid_time.astimezone(ZoneInfo("Asia/Taipei")).date()
            expected = expected_completed_session(generated_at)
            if valid_date > expected:
                return replace(entry, status="unverified", reason="future")
            if valid_date < expected:
                return replace(entry, status="stale", reason="stale")
            return entry
        evaluated.append(
            tuple(evaluate(entry) for entry in result.coverage)
        )
    market_coverage, institutional_coverage, margin_coverage = evaluated
    supplied = [entry for entries in evaluated for entry in entries]
    supplied.extend(entry for result in company_results for entry in result.coverage)
    supplied.extend(
        (
            _gate_entry("category.identity", market_result, market_coverage),
            _gate_entry("category.market", market_result, market_coverage),
            CoverageEntry(
                "category.technical",
                "failed" if technical_error else "computed",
                technical_error or "deterministic_daily_indicators",
                "computed",
                generated_at,
            ),
            _gate_entry("category.institutional", institutional_result, institutional_coverage),
            _gate_entry("category.margin", margin_result, margin_coverage),
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


def _market_conflicts(
    analysis: AnalysisResult,
    observations: tuple[NormalizedObservation, ...],
) -> tuple[str, ...]:
    conflicts: list[str] = []
    official_close = next(
        (item for item in observations if item.field_id == "market.close"),
        None,
    )
    if official_close is None or not isinstance(official_close.value, (int, float)):
        return ()
    official_date = official_close.valid_time.astimezone(ZoneInfo("Asia/Taipei")).date()
    if official_date != analysis.data_date:
        return ()
    tolerance = max(0.01, abs(float(official_close.value)) * 0.001)
    difference = abs(float(official_close.value) - analysis.close)
    if difference > tolerance:
        conflicts.append(
            "同一交易日收盤價衝突："
            f"官方 {float(official_close.value):g}、Yahoo {analysis.close:g}"
        )
    return tuple(conflicts)


def _failed_official(connector_id: str, when: datetime, reason: str, *, unresolved=False) -> ConnectorResult:
    definition = OFFICIAL_CONNECTORS[connector_id]
    source_id = "computed" if unresolved else definition.source_id
    return ConnectorResult(
        "unresolved_" + connector_id.split("_", 1)[1] if unresolved else connector_id,
        source_id, "" if unresolved else definition.endpoint, when, (),
        tuple(CoverageEntry(field, "failed", reason, source_id, when)
              for field in sorted(definition.contract_fields)), error=reason,
    )


def _fetch_official(connector_id, registry, symbol, timeout):
    when = datetime.now(timezone.utc)
    if connector_id not in registry:
        return _failed_official(connector_id, when, "connector_not_configured")
    try:
        return registry[connector_id].fetch_safe(symbol, timeout=timeout)
    except (ConnectorError, ValueError, OSError) as exc:
        return _failed_official(connector_id, when, str(exc))


def _resolve_profiles(results: tuple[CompanyResult, ...], when: datetime) -> str | None:
    """Require fresh, readable profiles from both markets and exactly one match.

    A missing or failed competing snapshot cannot establish exclusivity. This is
    current snapshot routing, not ISIN/transfer history or a historical lookup.
    """
    profiles = {r.market: r for r in results if r.connector_id.endswith("_profile")}
    if set(profiles) != set(ACTIVE_CONNECTORS):
        return None
    candidates = []
    today = when.astimezone(ZoneInfo("Asia/Taipei")).date()
    for market, profile in profiles.items():
        if profile.error or profile.source_date is None or not 0 <= (today - profile.source_date).days <= 3:
            return None
        if profile.fields.get("identity.legal_name"):
            if not any(c.field_id == "identity.legal_name" and c.status == "available" for c in profile.coverage):
                return None
            candidates.append(market)
        elif not all(c.reason == "no_matching_rows_in_snapshot" for c in profile.coverage):
            return None
    return candidates[0] if len(candidates) == 1 else None


def research_stock(
    symbol: str,
    *,
    timeout: float = 20.0,
    provider: YahooFinanceProvider | None = None,
    connectors: Mapping[str, ResearchConnector] | None = None,
    catalog: ResearchCatalog | None = None,
    generated_at: datetime | None = None,
    database_path: str | Path | None = None,
    company_connectors: Mapping[str, Any] | None = None,
) -> ResearchReport:
    """Build the first usable single-stock report from live and computed data."""

    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout 必須大於 0")
    when = generated_at or datetime.now(ZoneInfo("Asia/Taipei"))
    if when.tzinfo is None:
        raise ValueError("generated_at 必須包含時區")

    candidates = symbol_candidates(symbol)  # Validate before any external request.
    base_symbol = candidates[0].split(".", 1)[0]
    active_provider = provider if provider is not None else YahooFinanceProvider(timeout=timeout)
    market_data, analysis, analysis_error = None, None, None
    try:
        fetched = active_provider.fetch(symbol)
        if fetched.symbol.upper() not in candidates:
            raise DataProviderError("研究入口股票身分不一致")
        market_data = fetched
        analysis = analyze_bars(market_data, as_of=when)
    except (DataProviderError, ValueError, OSError) as exc:
        analysis_error = str(exc) or type(exc).__name__
    yahoo_fetched_at = datetime.now(timezone.utc)
    active_connectors = connectors if connectors is not None else OFFICIAL_CONNECTORS
    # Supplying test/replay connectors must not unexpectedly access live sources.
    supplements = company_connectors if company_connectors is not None else (COMPANY_CONNECTORS if connectors is None else {})
    company_results = None
    routing_error = None
    if len(candidates) == 1:
        market, resolution = _market_for_symbol(candidates[0]), "requested_market"
    elif market_data is not None:
        market, resolution = _market_for_symbol(market_data.symbol), "provider_symbol"
    else:
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = [executor.submit(fetch_company_sources, base_symbol, venue, timeout=timeout,
                                       connectors=supplements, as_of=when) for venue in ACTIVE_CONNECTORS]
            company_results = tuple(result for task in pending for result in task.result())
        market = _resolve_profiles(company_results, when) or "UNRESOLVED"
        resolution = "official_profiles" if market != "UNRESOLVED" else "unresolved"
        if market == "UNRESOLVED":
            routing_error = "市場未確認：官方主檔缺漏、失敗、過期或上市櫃匹配有歧義；可明確指定 .TW／.TWO，仍需查核身分"

    if market in ACTIVE_CONNECTORS:
        with ThreadPoolExecutor(max_workers=4) as executor:
            company_future = (executor.submit(fetch_company_sources, base_symbol, market, timeout=timeout,
                                              connectors=supplements, as_of=when) if company_results is None else None)
            pending = [executor.submit(_fetch_official, key, active_connectors, base_symbol, timeout)
                       for key in ACTIVE_CONNECTORS[market]]
            results = tuple(task.result() for task in pending)
            if company_future is not None:
                company_results = company_future.result()
    else:
        # Do not mix two markets' same field IDs or guess TWSE on an outage.
        results = tuple(_failed_official(key, when, "not_requested_market_unresolved", unresolved=True)
                        for key in ACTIVE_CONNECTORS["TWSE"])
    company_results = company_results or ()
    selected_company = tuple(r for r in company_results if r.market == market)
    resolved_symbol = base_symbol + (".TW" if market == "TWSE" else ".TWO" if market == "TPEx" else "")

    observations = tuple(item for result in results for item in result.observations)
    official_name = next(
        (
            str(item.value)
            for item in observations
            if item.field_id == "identity.name" and str(item.value).strip()
        ),
        next((str(r.fields["identity.legal_name"]) for r in selected_company if "identity.legal_name" in r.fields),
             analysis.name if analysis else base_symbol),
    )
    official_health = tuple(_health(result, when) if market in ACTIVE_CONNECTORS else
                            replace(_health(result, when), status="not_requested") for result in results)
    yahoo_status = analysis.freshness_status if analysis else "failed"
    if yahoo_status == "current":
        yahoo_status = "ok"
    yahoo_health = SourceHealth(
        connector_id="yahoo_daily",
        source_id="yahoo_finance",
        endpoint="Yahoo Finance Chart API",
        status=yahoo_status,
        fetched_at=yahoo_fetched_at,
        observations=len(market_data.bars) if market_data else 0,
        valid_times=(
            datetime.combine(
                analysis.data_date,
                datetime.min.time(),
                tzinfo=ZoneInfo("Asia/Taipei"),
            ),
        ) if analysis else (),
        error=analysis_error,
        valid_time_verified=analysis is not None,
        time_status=yahoo_status,
        completeness_status="complete" if analysis else "unavailable",
    )

    active_catalog = catalog if catalog is not None else load_catalog()
    coverage, by_category = _coverage_items(active_catalog, results, when, selected_company,
                                             technical_error=analysis_error if analysis is None else None)
    conflicts = _market_conflicts(analysis, observations) if analysis else ()
    if conflicts:
        coverage = tuple(
            replace(
                item,
                status="conflicting",
                reason="official_yahoo_same_session_close_conflict",
            )
            if item.field_id in {"category.market", "market.close"}
            else item
            for item in coverage
        )
        by_category = {}
        for item in coverage:
            counts = by_category.setdefault(item.category, {})
            counts[item.status] = counts.get(item.status, 0) + 1
    warnings: list[str] = []
    warnings.append("官方重大訊息只查詢回傳快照；未查到不代表沒有事件，尚未涵蓋完整歷史或外部新聞。")
    if any(source.market != market for source in company_results):
        warnings.append("公司快照含跨市場路由候選；非已選市場的資料只保留查詢紀錄，不合併為該股證據。")
    for source in company_results:
        if source.error:
            warnings.append(f"{source.connector_id} 失敗：{source.error}")
        warnings.extend(f"{source.connector_id}: {issue}" for issue in source.issues)
    if analysis_error:
        warnings.append("技術分析不可用：" + analysis_error)
    if routing_error:
        warnings.append(routing_error)
    if analysis and analysis.freshness_warning:
        warnings.append(analysis.freshness_warning)
    for source in official_health:
        if source.missing_fields:
            warnings.append(f"{source.connector_id} 不可用欄位：{', '.join(source.missing_fields)}")
        if source.status == "failed":
            warnings.append(f"{source.connector_id} 失敗：{source.error}")
        elif source.status == "stale":
            warnings.append(
                f"{source.connector_id} 含 {source.valid_times[0].date().isoformat()} 資料，可能已過期"
            )
        elif source.status == "unverified_time":
            warnings.append(f"{source.connector_id} 回應缺少可驗證資料日期")
        elif source.status == "future":
            warnings.append(f"{source.connector_id} 含晚於應完成交易日的資料")
    missing_p0_items = [
        item for item in coverage
        if item.field_id.startswith("category.")
        and item.priority == "P0"
        and (
            item.implementation != "implemented"
            or item.status not in {"available", "computed", "not_applicable"}
        )
    ]
    missing_p0_names = [item.name for item in missing_p0_items]
    if missing_p0_names:
        warnings.append("尚未涵蓋的 P0 類別：" + "、".join(missing_p0_names))

    warnings.extend(conflicts)
    blockers: list[str] = []
    if analysis:
        blockers.extend(f"技術初篩不合格：{reason}" for reason in analysis.screening_blockers)
    else:
        blockers.append("技術分析不可用：" + (analysis_error or "unknown_error"))
    if routing_error:
        blockers.append(routing_error)
    blockers.extend(f"P0 類別未就緒：{item.name}" for item in missing_p0_items)
    try:
        assert_strict_p0_coverage(active_catalog, (
            CoverageEntry(item.field_id, item.status, item.reason, item.source_id, item.observed_at)
            for item in coverage
        ))
    except CoverageError as exc:
        blockers.append(str(exc))
    if analysis and analysis.freshness_status != "current":
        blockers.append(f"Yahoo 日線狀態為 {analysis.freshness_status}")
    blockers.extend(conflicts)
    for source in official_health:
        if source.status in {"failed", "future", "unverified_time"}:
            blockers.append(f"{source.connector_id} 狀態為 {source.status}")

    active_status = "insufficient" if blockers else "complete"
    if active_status == "complete" and (
        warnings or any(source.status != "ok" for source in official_health)
    ):
        active_status = "partial"
    report = ResearchReport(
        status=active_status,
        generated_at=when,
        symbol=resolved_symbol,
        name=official_name,
        market=market,
        analysis=analysis,
        official_observations=observations,
        official_findings=_official_findings(observations),
        source_health=(yahoo_health, *official_health),
        coverage=coverage,
        coverage_by_category=by_category,
        decision_blockers=tuple(dict.fromkeys(blockers)),
        market_conflicts=conflicts,
        warnings=tuple(warnings),
        company_sources=company_results,
        analysis_error=analysis_error,
        market_resolution=resolution,
        routing_error=routing_error,
    )
    if database_path is not None:
        run_id = persist_research_report(report, database_path)
        report = replace(report, run_id=run_id, database_path=str(database_path))
    return report


_EVIDENCE_QUALITY_OFFICIAL = {
    "source_reliability": 0.95,
    "claim_support": 1.0,
    "independence": 1.0,
    "recency": 0.9,
    "relevance": 1.0,
    "directness": 1.0,
    "corroboration": 0.5,
}


def persist_research_report(report: ResearchReport, path: str | Path) -> str:
    """Persist one report with bitemporal observations and linked evidence."""

    with ResearchDatabase(path) as database:
        database.migrate()
        database.upsert_source(
            "twse_openapi", "臺灣證券交易所官方資料服務", "official",
            "https://openapi.twse.com.tw/v1",
        )
        database.upsert_source(
            "tpex_openapi", "證券櫃檯買賣中心 OpenAPI", "official",
            "https://www.tpex.org.tw/openapi/v1",
        )
        database.upsert_source(
            "yahoo_finance", "Yahoo Finance Chart API", "third_party",
            "https://query1.finance.yahoo.com/v8/finance/chart",
        )
        database.upsert_source("computed", "Python 可重現計算", "internal")

        base_symbol = report.symbol.split(".", 1)[0]
        instrument_id = database.get_or_create_instrument(
            base_symbol,
            report.market,
            report.name,
            report.generated_at,
        ) if report.market in ACTIVE_CONNECTORS else None
        run_id = database.start_run(
            base_symbol,
            report.generated_at,
            report.market,
            depth="first_batch",
        )
        try:
            document_by_prefix: dict[str, str] = {}
            fetched_at_by_prefix: dict[str, datetime] = {}
            yahoo_retrieved_at = report.generated_at
            for source in report.source_health:
                if source.connector_id == "yahoo_daily":
                    yahoo_retrieved_at = source.fetched_at
                if "market" in source.connector_id:
                    fetched_at_by_prefix["identity"] = source.fetched_at
                    fetched_at_by_prefix["market"] = source.fetched_at
                elif "institutional" in source.connector_id:
                    fetched_at_by_prefix["institutional"] = source.fetched_at
                elif "margin" in source.connector_id:
                    fetched_at_by_prefix["margin"] = source.fetched_at
                if source.content_hash is None:
                    continue
                document_id = database.add_raw_document(
                    source_id=source.source_id,
                    url=source.endpoint,
                    content_hash=source.content_hash,
                    retrieved_at=source.fetched_at,
                    event_time=source.valid_times[-1] if source.valid_times else None,
                    title=source.connector_id,
                )
                if "market" in source.connector_id:
                    document_by_prefix["identity"] = document_id
                    document_by_prefix["market"] = document_id
                elif "institutional" in source.connector_id:
                    document_by_prefix["institutional"] = document_id
                elif "margin" in source.connector_id:
                    document_by_prefix["margin"] = document_id

            observation_ids: dict[tuple[str, str], str] = {}
            for observation in report.official_observations:
                prefix = observation.field_id.split(".", 1)[0]
                observation_id = database.add_observation(
                    instrument_id=instrument_id,
                    field_id=observation.field_id,
                    value=observation.value,
                    unit=observation.unit,
                    valid_time=observation.valid_time,
                    known_at=fetched_at_by_prefix.get(prefix, report.generated_at),
                    source_id=observation.source_id,
                    document_id=document_by_prefix.get(prefix),
                    source_record_key=observation.source_record_key,
                )
                observation_ids[(observation.field_id, observation.source_id)] = observation_id

            yahoo_close_id = None
            if report.analysis is not None:
                yahoo_valid_time = datetime.combine(
                    report.analysis.data_date, datetime.min.time(), tzinfo=ZoneInfo("Asia/Taipei"),
                )
                yahoo_close_id = database.add_observation(
                    instrument_id=instrument_id, field_id="market.close", value=report.analysis.close,
                    unit="TWD/share", valid_time=yahoo_valid_time, known_at=yahoo_retrieved_at,
                    source_id="yahoo_finance", source_record_key=report.symbol,
                )

            coverage_rows = (
                CoverageEntry(
                    item.field_id,
                    item.status,
                    item.reason,
                    item.source_id,
                    item.observed_at,
                )
                for item in report.coverage
            )
            database.save_coverage(run_id, coverage_rows)

            evidence_ids: list[str] = []
            for observation in report.official_observations:
                evidence_ids.append(
                    database.add_evidence(
                        run_id=run_id,
                        statement=f"{observation.field_id} = {observation.value} {observation.unit}",
                        claim_type="FACT",
                        stance="neutral",
                        source_id=observation.source_id,
                        retrieved_at=fetched_at_by_prefix.get(
                            observation.field_id.split(".", 1)[0],
                            report.generated_at,
                        ),
                        event_time=observation.valid_time,
                        quality=_EVIDENCE_QUALITY_OFFICIAL,
                        document_id=document_by_prefix.get(
                            observation.field_id.split(".", 1)[0]
                        ),
                        observation_ids=(
                            observation_ids[(observation.field_id, observation.source_id)],
                        ),
                    )
                )
            if report.analysis is not None and yahoo_close_id is not None:
                evidence_ids.append(database.add_evidence(
                    run_id=run_id,
                    statement=f"Yahoo 日線 {report.analysis.data_date.isoformat()} 收盤 {report.analysis.close:g}",
                    claim_type="FACT", stance="neutral", source_id="yahoo_finance",
                    retrieved_at=yahoo_retrieved_at, event_time=yahoo_valid_time,
                    quality={**_EVIDENCE_QUALITY_OFFICIAL, "source_reliability": 0.7},
                    observation_ids=(yahoo_close_id,),
                ))
            for supplement in report.company_sources:
                database.save_supplement(run_id, supplement.to_dict())
                document_id = None
                if supplement.content_hash:
                    document_id = database.add_raw_document(
                        source_id=supplement.source_id, url=supplement.endpoint,
                        content_hash=supplement.content_hash, retrieved_at=supplement.fetched_at,
                        title=supplement.connector_id, parser_version="company-1",
                    )
                statuses = {item.field_id: item.status for item in supplement.coverage}
                if supplement.market != report.market:
                    # Routing candidates are saved separately, not claimed as the resolved company's facts.
                    continue
                for field, value in supplement.fields.items():
                    evidence_ids.append(database.add_evidence(
                        run_id=run_id, statement=f"公司主檔 {field} = {value}（出表日 {supplement.source_date or '未知'}）",
                        claim_type="FACT" if statuses.get(field) == "available" else "UNKNOWN",
                        stance="neutral", source_id=supplement.source_id,
                        retrieved_at=supplement.fetched_at, document_id=document_id,
                        quality={**_EVIDENCE_QUALITY_OFFICIAL, "claim_support": 0.0,
                                 "recency": 0.0, "independence": 0.0, "corroboration": 0.0},
                        evidence_status="profile_metadata_unassessed",
                    ))
                for event in supplement.announcements:
                    evidence_ids.append(database.add_evidence(
                        run_id=run_id, statement=f"官方公告主旨（未核實內容）：{event.headline}｜記錄鍵 {event.source_record_key}",
                        claim_type="UNKNOWN" if event.issues else "FACT", stance="neutral",
                        source_id=supplement.source_id, retrieved_at=supplement.fetched_at,
                        published_at=event.published_at, document_id=document_id,
                        quality={**_EVIDENCE_QUALITY_OFFICIAL, "claim_support": 0.0,
                                 "recency": 0.0, "independence": 0.0, "corroboration": 0.0},
                        evidence_status="metadata_only_unassessed",
                    ))
            database.add_conclusion(
                run_id,
                "decision_eligibility",
                "eligible" if report.decision_eligible else "blocked",
                "medium" if report.decision_eligible else "low",
                evidence_ids,
                details={
                    "status": report.status,
                    "decision_blockers": list(report.decision_blockers),
                    "market_conflicts": list(report.market_conflicts),
                    "analysis_snapshot": report.analysis.to_dict(decision_eligible=False) if report.analysis else None,
                    "analysis_error": report.analysis_error,
                    "market_resolution": report.market_resolution,
                    "routing_error": report.routing_error,
                    "source_health_snapshot": [item.to_dict() for item in report.source_health],
                    "full_inputs_replayable": False,
                },
            )
            database.finish_run(run_id, report.status)
        except Exception:
            database.finish_run(run_id, "failed")
            raise
    return run_id
