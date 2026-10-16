from __future__ import annotations


def sma(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"至少需要 {period} 筆資料")
    return sum(values[-period:]) / period


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        raise ValueError("EMA 需要至少一筆資料")
    multiplier = 2.0 / (period + 1.0)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        raise ValueError(f"RSI 至少需要 {period + 1} 筆資料")
    changes = [current - previous for previous, current in zip(values, values[1:])]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    relative_strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> float:
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("OHLC 資料長度不一致")
    if len(closes) <= period:
        raise ValueError(f"ATR 至少需要 {period + 1} 筆資料")
    true_ranges: list[float] = []
    for index in range(1, len(closes)):
        true_ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )
    average = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        average = ((average * (period - 1)) + true_range) / period
    return average


def macd(values: list[float]) -> tuple[float, float, float]:
    if len(values) < 35:
        raise ValueError("MACD 至少需要 35 筆資料")
    fast = ema_series(values, 12)
    slow = ema_series(values, 26)
    line = [fast_value - slow_value for fast_value, slow_value in zip(fast, slow)]
    signal = ema_series(line, 9)
    return line[-1], signal[-1], line[-1] - signal[-1]
