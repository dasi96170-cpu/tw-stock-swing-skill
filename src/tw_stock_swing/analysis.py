from __future__ import annotations

import math
from statistics import median
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .indicators import atr, macd, rsi, sma
from .models import AnalysisResult, MarketData, PriceBar


MINIMUM_BARS = 60
MARKET_CLOSE = time(13, 35)
MIN_MEDIAN_TURNOVER_PROXY = 10_000_000.0  # TWD; engineering guard, not calibrated alpha.


def _tick_size(price: float) -> float:
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    if price < 1000:
        return 1.0
    return 5.0


def round_to_tick(price: float, mode: str = "nearest") -> float:
    tick = _tick_size(max(price, 0.01))
    units = price / tick
    if mode == "down":
        rounded = math.floor(units + 1e-9) * tick
    elif mode == "up":
        rounded = math.ceil(units - 1e-9) * tick
    else:
        rounded = round(units) * tick
    decimals = 2 if tick < 0.1 else 1 if tick < 1 else 0
    return round(max(rounded, tick), decimals)


def _validate_bars(bars: tuple[PriceBar, ...]) -> None:
    if len(bars) < MINIMUM_BARS:
        raise ValueError(f"有效日線只有 {len(bars)} 筆，至少需要 {MINIMUM_BARS} 筆")
    if any(current.date <= previous.date for previous, current in zip(bars, bars[1:])):
        raise ValueError("日線日期必須嚴格遞增且不可重複")
    for bar in bars:
        if (
            not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) for value in (bar.open, bar.high, bar.low, bar.close))
            or bar.low <= 0
            or bar.high < bar.low
            or not (bar.low <= bar.open <= bar.high)
            or not (bar.low <= bar.close <= bar.high)
        ):
            raise ValueError(f"{bar.date.isoformat()} 的 OHLC 資料不合理")
        if (isinstance(bar.volume, bool) or not isinstance(bar.volume, (int, float))
                or not math.isfinite(bar.volume) or bar.volume < 0
                or int(bar.volume) != bar.volume):
            raise ValueError(f"{bar.date.isoformat()} 的成交量必須是非負有限整數")
        if bar.adjusted_close is not None and (
            not math.isfinite(bar.adjusted_close) or bar.adjusted_close <= 0
        ):
            raise ValueError(f"{bar.date.isoformat()} 的還原收盤價無效")


def _previous_weekday(value: date) -> date:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def expected_completed_session(as_of: datetime) -> date:
    """Return the latest session that should be complete.

    This is deliberately a weekday approximation until an exchange calendar is
    added.  It never treats the current trading day's partial daily candle as a
    completed observation.
    """
    local = as_of.astimezone(ZoneInfo("Asia/Taipei"))
    if local.date().weekday() >= 5:
        candidate = local.date()
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate
    if local.time() < MARKET_CLOSE:
        return _previous_weekday(local.date())
    return local.date()


def analyze_bars(
    data: MarketData,
    *,
    as_of: datetime | None = None,
    exclude_incomplete_bar: bool = True,
    min_median_turnover_proxy: float = MIN_MEDIAN_TURNOVER_PROXY,
) -> AnalysisResult:
    if (isinstance(min_median_turnover_proxy, bool)
            or not math.isfinite(min_median_turnover_proxy) or min_median_turnover_proxy <= 0):
        raise ValueError("流動性估計門檻必須為正的有限數值")
    when = as_of or datetime.now(ZoneInfo("Asia/Taipei"))
    if when.tzinfo is None:
        raise ValueError("as_of 必須包含時區")
    local_when = when.astimezone(ZoneInfo("Asia/Taipei"))
    bars = data.bars
    if data.quality_issues:
        raise ValueError("日線品質未通過：" + "; ".join(data.quality_issues))
    # Current adjusted histories are not point-in-time snapshots. Reject rather
    # than silently claiming historical analysis from today's revised inputs.
    if any(bar.date > local_when.date() for bar in bars):
        raise ValueError("日線包含截止時間之後的資料；不支援以目前行情進行歷史 PIT 分析")
    excluded_latest = False
    if (
        exclude_incomplete_bar
        and bars
        and bars[-1].date == local_when.date()
        and local_when.date().weekday() < 5
        and local_when.time() < MARKET_CLOSE
    ):
        bars = bars[:-1]
        excluded_latest = True
    _validate_bars(bars)
    if all(bar.adjusted_close is None for bar in bars):
        raise ValueError("還原價格全部缺漏，無法確認公司行動調整基準")
    if any(bar.adjusted_close is not None for bar in bars) and any(
        bar.adjusted_close is None for bar in bars
    ):
        raise ValueError("還原價格部分缺漏，不可混用還原與未還原基準")
    adjustment_factors = [
        (bar.adjusted_close / bar.close)
        if bar.adjusted_close is not None and bar.adjusted_close > 0
        else 1.0
        for bar in bars
    ]
    closes = [bar.close * factor for bar, factor in zip(bars, adjustment_factors)]
    highs = [bar.high * factor for bar, factor in zip(bars, adjustment_factors)]
    lows = [bar.low * factor for bar, factor in zip(bars, adjustment_factors)]
    volumes = [bar.volume for bar in bars]
    turnover_proxies = [bar.close * bar.volume for bar in bars]
    if any(not math.isfinite(value) for value in turnover_proxies):
        raise ValueError("成交金額估計超過有限數值範圍")
    median_turnover = median(turnover_proxies[-20:])
    previous_turnover = sum(turnover_proxies[-21:-1]) / 20
    turnover_ratio = turnover_proxies[-1] / previous_turnover if previous_turnover > 0 else 0.0
    # Dividend/split adjustment changes are not a verified split event feed.
    # Do not manufacture volume signals across such a change.
    recent_factors = adjustment_factors[-21:]
    volume_signal_usable = all(math.isclose(f, recent_factors[-1], rel_tol=1e-4)
                               for f in recent_factors)
    screening_blockers: list[str] = []
    if median_turnover < min_median_turnover_proxy:
        screening_blockers.append("low_median_turnover_proxy")
    if any(bar.volume == 0 for bar in bars[-20:]):
        screening_blockers.append("zero_volume_in_recent_window")
    # These are possible gaps, NOT an assertion that a weekday was a session.
    weekday_gaps = []
    for previous, current in zip(bars, bars[1:]):
        day = previous.date + timedelta(days=1)
        while day < current.date:
            if day.weekday() < 5:
                weekday_gaps.append(day.isoformat())
            day += timedelta(days=1)
    if weekday_gaps:
        screening_blockers.append("unverified_session_gaps")

    latest = bars[-1]
    sma5 = sma(closes, 5)
    sma10 = sma(closes, 10)
    sma20 = sma(closes, 20)
    sma60 = sma(closes, 60)
    rsi14 = rsi(closes, 14)
    adjusted_atr14 = atr(highs, lows, closes, 14)
    latest_factor = adjustment_factors[-1]
    atr14 = adjusted_atr14 / latest_factor
    macd_line, macd_signal, macd_histogram = macd(closes)
    latest_adjusted_close = closes[-1]
    previous_20_high = max(highs[-21:-1]) if len(highs) >= 21 else max(highs[:-1])
    average_volume_20 = sum(volumes[-21:-1]) / min(20, len(volumes) - 1)
    volume_ratio = latest.volume / average_volume_20 if average_volume_20 > 0 else 0.0
    change_20d = ((latest_adjusted_close / closes[-21]) - 1.0) * 100.0

    score = 20
    reasons: list[str] = []
    risks: list[str] = []

    if latest_adjusted_close > sma20:
        score += 15
        reasons.append("收盤價站上 20 日均線")
    else:
        score -= 10
        risks.append("收盤價仍在 20 日均線下方")

    if sma5 > sma10 > sma20:
        score += 15
        reasons.append("5、10、20 日均線呈多頭排列")
    elif sma5 < sma10 < sma20:
        score -= 10
        risks.append("短中期均線呈空頭排列")

    if sma20 > sma60:
        score += 10
        reasons.append("20 日均線高於 60 日均線，中期趨勢偏多")
    else:
        risks.append("20 日均線尚未站上 60 日均線")

    breakout_ratio = latest_adjusted_close / previous_20_high
    if breakout_ratio > 1.0:
        score += 15
        reasons.append("收盤價突破前 20 日高點")
    elif breakout_ratio >= 0.97:
        score += 8
        reasons.append("價格接近前 20 日高點")

    if (volume_signal_usable and turnover_ratio >= 1.5
            and latest_adjusted_close > closes[-2] and latest.close > latest.open
            and latest.close >= (latest.high + latest.low) / 2):
        score += 10
        reasons.append(f"上漲且收於區間上半部，成交金額估計為前 20 筆均值的 {turnover_ratio:.2f} 倍（非官方成交額）")
    elif volume_signal_usable and turnover_ratio < 0.7:
        score -= 5
        risks.append("成交金額估計明顯低於前 20 筆均值（非官方成交額）")
    elif not volume_signal_usable:
        risks.append("近期價格調整因子改變，停用量值訊號加減分；尚待公司行動核對")
    elif turnover_ratio >= 1.5:
        risks.append("成交金額估計放大但未滿足上漲確認，不作看多加分")

    if 50 <= rsi14 <= 70:
        score += 10
        reasons.append(f"RSI14 為 {rsi14:.1f}，動能偏多且未明顯過熱")
    elif 40 <= rsi14 < 50 or 70 < rsi14 <= 78:
        score += 4
    elif rsi14 > 78:
        score -= 8
        risks.append(f"RSI14 為 {rsi14:.1f}，短線可能過熱")
    else:
        score -= 5
        risks.append(f"RSI14 為 {rsi14:.1f}，動能偏弱")

    if macd_line > macd_signal and macd_histogram > 0:
        score += 10
        reasons.append("MACD 位於訊號線上方")
    else:
        risks.append("MACD 尚未形成正向動能")

    if change_20d > 0:
        score += 5
        reasons.append(f"20 筆觀察間隔報酬為 {change_20d:.1f}%（交易日曆未驗證）")
    else:
        score -= 5
        risks.append(f"20 筆觀察間隔報酬為 {change_20d:.1f}%（交易日曆未驗證）")

    atr_pct = (atr14 / latest.close) * 100.0
    if atr_pct > 6:
        score -= 8
        risks.append(f"ATR 約占股價 {atr_pct:.1f}%，波動風險偏高")
    elif atr_pct > 4:
        risks.append(f"ATR 約占股價 {atr_pct:.1f}%，波動較大")

    score = max(0, min(100, score))
    verdict = "候選" if score >= 70 else "觀察" if score >= 55 else "避開"
    if screening_blockers:
        verdict = "不納入"
    if "low_median_turnover_proxy" in screening_blockers:
        risks.append(f"近 20 筆成交金額估計中位數 {median_turnover:,.0f} 元低於 {min_median_turnover_proxy:,.0f} 元工程門檻；非官方成交額或成交容量保證")
    if "zero_volume_in_recent_window" in screening_blockers:
        risks.append("近 20 筆含零成交，無法確認可交易性")
    if weekday_gaps:
        risks.append("中間平日缺列，無官方日曆區分休市／停牌／來源缺檔，暫不納入排名")
    risks.append("交易日曆與個股停復牌尚未驗證；期間以觀察筆數計，不保證為連續交易日")

    recent_support = min(lows[-10:]) / latest_factor
    volatility_stop = latest.close - (2.0 * atr14)
    stop = max(recent_support, volatility_stop)
    if stop >= latest.close - (0.5 * atr14):
        stop = latest.close - (1.5 * atr14)
    stop_loss = round_to_tick(stop, "down")
    entry_low = round_to_tick(latest.close - (0.25 * atr14), "down")
    entry_high = round_to_tick(latest.close + (0.10 * atr14), "up")
    risk_per_share = max(entry_high - stop_loss, _tick_size(entry_high))
    target_1 = round_to_tick(entry_high + (2.0 * risk_per_share), "up")
    target_2 = round_to_tick(entry_high + (3.0 * risk_per_share), "up")
    rounded_close = round_to_tick(latest.close)
    risk_reward_1 = (target_1 - entry_high) / risk_per_share

    expected_session = expected_completed_session(local_when)
    freshness_warning: str | None = None
    freshness_status = "current"
    if latest.date > expected_session:
        freshness_status = "future"
        freshness_warning = "資料日期晚於目前日期，請檢查資料源時鐘"
    elif latest.date < expected_session:
        freshness_status = "stale"
        freshness_warning = (
            f"最新完整日線為 {latest.date.isoformat()}，"
            f"落後預期交易日 {expected_session.isoformat()}"
        )
    elif excluded_latest:
        freshness_warning = "已排除盤中尚未完成的當日日 K，技術分析使用上一個完整交易日"

    if not risks:
        risks.append("技術面分數不涵蓋重大消息、財報、處置股票及大盤突發風險")
    else:
        risks.append("模型未納入重大消息、財報與處置／注意股票資訊")

    return AnalysisResult(
        symbol=data.symbol,
        name=data.name,
        data_date=latest.date,
        source=data.source,
        score=score,
        verdict=verdict,
        close=rounded_close,
        change_20d_pct=round(change_20d, 2),
        volume_ratio=round(volume_ratio, 2),
        sma5=round(sma5, 2),
        sma10=round(sma10, 2),
        sma20=round(sma20, 2),
        sma60=round(sma60, 2),
        rsi14=round(rsi14, 2),
        macd=round(macd_line, 3),
        macd_signal=round(macd_signal, 3),
        atr14=round(atr14, 2),
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        target_1=target_1,
        target_2=target_2,
        risk_reward_1=round(risk_reward_1, 2),
        excluded_incomplete_bar=excluded_latest,
        freshness_status=freshness_status,
        reasons=tuple(reasons),
        risks=tuple(risks),
        freshness_warning=freshness_warning,
        screening_eligible=not screening_blockers,
        screening_blockers=tuple(screening_blockers),
        median_turnover_proxy_20=round(median_turnover, 2),
        min_median_turnover_proxy=min_median_turnover_proxy,
        turnover_proxy_ratio=round(turnover_ratio, 4),
        volume_signal_usable=volume_signal_usable,
        unverified_weekday_gaps=tuple(weekday_gaps),
    )
