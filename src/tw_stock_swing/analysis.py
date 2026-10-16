from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from .indicators import atr, macd, rsi, sma
from .models import AnalysisResult, MarketData, PriceBar


MINIMUM_BARS = 60


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
            bar.low <= 0
            or bar.high < bar.low
            or not (bar.low <= bar.open <= bar.high)
            or not (bar.low <= bar.close <= bar.high)
        ):
            raise ValueError(f"{bar.date.isoformat()} 的 OHLC 資料不合理")
        if bar.volume < 0:
            raise ValueError(f"{bar.date.isoformat()} 的成交量不可為負數")


def analyze_bars(data: MarketData) -> AnalysisResult:
    bars = data.bars
    _validate_bars(bars)
    closes = [bar.close for bar in bars]
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    volumes = [bar.volume for bar in bars]

    latest = bars[-1]
    sma5 = sma(closes, 5)
    sma10 = sma(closes, 10)
    sma20 = sma(closes, 20)
    sma60 = sma(closes, 60)
    rsi14 = rsi(closes, 14)
    atr14 = atr(highs, lows, closes, 14)
    macd_line, macd_signal, macd_histogram = macd(closes)
    previous_20_high = max(highs[-21:-1]) if len(highs) >= 21 else max(highs[:-1])
    average_volume_20 = sum(volumes[-21:-1]) / min(20, len(volumes) - 1)
    volume_ratio = latest.volume / average_volume_20 if average_volume_20 > 0 else 0.0
    change_20d = ((latest.close / closes[-21]) - 1.0) * 100.0

    score = 20
    reasons: list[str] = []
    risks: list[str] = []

    if latest.close > sma20:
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

    breakout_ratio = latest.close / previous_20_high
    if breakout_ratio >= 1.0:
        score += 15
        reasons.append("收盤價突破前 20 日高點")
    elif breakout_ratio >= 0.97:
        score += 8
        reasons.append("價格接近前 20 日高點")

    if volume_ratio >= 1.5:
        score += 10
        reasons.append(f"成交量為 20 日均量的 {volume_ratio:.2f} 倍")
    elif volume_ratio < 0.7:
        score -= 5
        risks.append("成交量明顯低於 20 日均量")

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
        reasons.append(f"20 個交易日漲幅為 {change_20d:.1f}%")
    else:
        score -= 5
        risks.append(f"20 個交易日報酬為 {change_20d:.1f}%")

    atr_pct = (atr14 / latest.close) * 100.0
    if atr_pct > 6:
        score -= 8
        risks.append(f"ATR 約占股價 {atr_pct:.1f}%，波動風險偏高")
    elif atr_pct > 4:
        risks.append(f"ATR 約占股價 {atr_pct:.1f}%，波動較大")

    score = max(0, min(100, score))
    verdict = "候選" if score >= 70 else "觀察" if score >= 55 else "避開"

    recent_support = min(lows[-10:])
    volatility_stop = latest.close - (2.0 * atr14)
    stop = max(recent_support, volatility_stop)
    if stop >= latest.close - (0.5 * atr14):
        stop = latest.close - (1.5 * atr14)
    stop_loss = round_to_tick(stop, "down")
    entry_low = round_to_tick(latest.close - (0.25 * atr14), "down")
    entry_high = round_to_tick(latest.close + (0.10 * atr14), "up")
    risk_per_share = max(latest.close - stop_loss, _tick_size(latest.close))
    target_1 = round_to_tick(latest.close + (2.0 * risk_per_share), "up")
    target_2 = round_to_tick(latest.close + (3.0 * risk_per_share), "up")
    rounded_close = round_to_tick(latest.close)
    actual_risk = max(rounded_close - stop_loss, _tick_size(latest.close))
    risk_reward_1 = (target_1 - rounded_close) / actual_risk

    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    age_days = (today - latest.date).days
    freshness_warning: str | None = None
    if age_days < 0:
        freshness_warning = "資料日期晚於目前日期，請檢查資料源時鐘"
    elif age_days > 5:
        freshness_warning = f"最新資料距今 {age_days} 天，可能已過期"

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
        reasons=tuple(reasons),
        risks=tuple(risks),
        freshness_warning=freshness_warning,
    )
