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


@dataclass(frozen=True, slots=True)
class MarketData:
    symbol: str
    name: str
    bars: tuple[PriceBar, ...]
    source: str


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
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    freshness_warning: str | None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["data_date"] = self.data_date.isoformat()
        data["reasons"] = list(self.reasons)
        data["risks"] = list(self.risks)
        return data
