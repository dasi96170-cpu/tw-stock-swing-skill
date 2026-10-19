from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True, slots=True)
class PriceBar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    adjusted_close: float | None = None


@dataclass(frozen=True, slots=True)
class MarketData:
    symbol: str
    name: str
    bars: tuple[PriceBar, ...]
    source: str
    quality_issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    symbol: str
    name: str
    data_date: date
    source: str
    score: int
    verdict: str
    close: float
    change_20d_pct: float
    volume_ratio: float
    sma5: float
    sma10: float
    sma20: float
    sma60: float
    rsi14: float
    macd: float
    macd_signal: float
    atr14: float
    entry_low: float
    entry_high: float
    stop_loss: float
    target_1: float
    target_2: float
    risk_reward_1: float
    excluded_incomplete_bar: bool
    freshness_status: str
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    freshness_warning: str | None
    screening_eligible: bool = True
    screening_blockers: tuple[str, ...] = ()
    screening_policy_version: str = "selection-safety-1"
    median_turnover_proxy_20: float = 0.0
    min_median_turnover_proxy: float = 10_000_000.0
    turnover_proxy_ratio: float = 0.0
    volume_signal_usable: bool = False
    session_calendar_verified: bool = False
    unverified_weekday_gaps: tuple[str, ...] = ()
    return_window_observations: int = 20

    def to_dict(self, *, decision_eligible: bool = False) -> dict[str, Any]:
        data = asdict(self)
        data["data_date"] = self.data_date.isoformat()
        data["reasons"] = list(self.reasons)
        data["risks"] = list(self.risks)
        if not decision_eligible or not self.screening_eligible or self.freshness_status != "current":
            for field in ("entry_low", "entry_high", "stop_loss", "target_1", "target_2", "risk_reward_1"):
                data[field] = None
        return data
