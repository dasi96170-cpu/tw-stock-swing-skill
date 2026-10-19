from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .models import MarketData, PriceBar


class DataProviderError(RuntimeError):
    """Raised when market data cannot be retrieved or parsed."""


MAX_YAHOO_RESPONSE_BYTES = 10 * 1024 * 1024


def symbol_candidates(symbol: str) -> tuple[str, ...]:
    normalized = symbol.strip().upper()
    if re.fullmatch(r"\d{4,6}\.(TW|TWO)", normalized):
        return (normalized,)
    if re.fullmatch(r"\d{4,6}", normalized):
        return (f"{normalized}.TW", f"{normalized}.TWO")
    raise ValueError("台股代號必須是 4–6 位數字，可選擇加上 .TW 或 .TWO")


class YahooFinanceProvider:
    """Fetch daily OHLCV data from the Yahoo Finance chart endpoint."""

    source_name = "Yahoo Finance Chart API（日線，非交易所即時報價）"

    def __init__(
        self,
        timeout: float = 15.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout 必須大於 0")
        self.timeout = timeout
        self._opener = opener

    def fetch(self, symbol: str, range_: str = "6mo") -> MarketData:
        errors: list[str] = []
        for candidate in symbol_candidates(symbol):
            try:
                return self._fetch_candidate(candidate, range_)
            except DataProviderError as exc:
                errors.append(f"{candidate}: {exc}")
        raise DataProviderError("；".join(errors))

    def _fetch_candidate(self, symbol: str, range_: str) -> MarketData:
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{quote(symbol)}?range={quote(range_)}&interval=1d&events=history"
        )
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "tw-stock-swing-skill/0.1 (+research-use)",
            },
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                final_url = response.geturl() if hasattr(response, "geturl") else url
                if urlparse(final_url).hostname != urlparse(url).hostname:
                    raise DataProviderError("Yahoo 回應重新導向至未允許網域")
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise DataProviderError("Yahoo 回應的 Content-Length 無效") from exc
                    if declared_size > MAX_YAHOO_RESPONSE_BYTES:
                        raise DataProviderError("Yahoo 回應大小超過限制")
                raw = response.read(MAX_YAHOO_RESPONSE_BYTES + 1)
                if len(raw) > MAX_YAHOO_RESPONSE_BYTES:
                    raise DataProviderError("Yahoo 回應大小超過限制")
                payload = json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            raise DataProviderError(f"HTTP {exc.code}") from exc
        except URLError as exc:
            raise DataProviderError(f"網路錯誤：{exc.reason}") from exc
        except (TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataProviderError(f"回應無法讀取：{exc}") from exc
        if not isinstance(payload, dict):
            raise DataProviderError("Yahoo 回應不是 JSON 物件")
        return self._parse(payload, symbol)

    def _parse(self, payload: dict[str, Any], requested_symbol: str) -> MarketData:
        try:
            return self._parse_payload(payload, requested_symbol)
        except (TypeError, ValueError, KeyError, IndexError, AttributeError, OverflowError, OSError) as exc:
            raise DataProviderError(f"Yahoo 資料格式或數值錯誤：{exc}") from exc

    def _parse_payload(self, payload: dict[str, Any], requested_symbol: str) -> MarketData:
        chart = payload.get("chart") or {}
        if chart.get("error"):
            description = chart["error"].get("description", "未知資料錯誤")
            raise DataProviderError(description)
        results = chart.get("result") or []
        if not results:
            raise DataProviderError("查無資料")
        result = results[0]
        meta = result.get("meta") or {}
        symbol = str(meta.get("symbol") or "").upper()
        if symbol != requested_symbol.upper():
            raise DataProviderError(f"股票身分不一致：請求 {requested_symbol}，回應 {symbol or '缺少代號'}")
        if meta.get("currency") != "TWD" or meta.get("instrumentType") != "EQUITY":
            raise DataProviderError("幣別／證券類型未通過：目前僅接受明確標示 TWD／EQUITY 的台股日線")
        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators") or {}
        quotes = (indicators.get("quote") or [{}])[0]
        adjusted = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []
        fields = {key: quotes.get(key) or [] for key in ("open", "high", "low", "close", "volume")}
        timezone = ZoneInfo("Asia/Taipei")
        bars: list[PriceBar] = []
        issues: list[str] = []
        for field, values in fields.items():
            if len(values) != len(timestamps):
                issues.append(f"{field}: 欄位長度與 timestamp 不一致")
        if adjusted and len(adjusted) != len(timestamps):
            issues.append("adjclose: 欄位長度與 timestamp 不一致")
        for index, timestamp in enumerate(timestamps):
            try:
                values = {key: fields[key][index] for key in fields}
            except IndexError:
                issues.append(f"row={index}, timestamp={timestamp}: 欄位長度不一致")
                continue
            if any(value is None for value in values.values()):
                issues.append(f"row={index}, timestamp={timestamp}: OHLCV 缺值")
                continue
            if any(isinstance(value, bool) for value in values.values()):
                raise DataProviderError(f"row={index}: OHLCV 不可為布林值")
            high = float(values["high"])
            low = float(values["low"])
            close = float(values["close"])
            open_price = float(values["open"])
            if (
                not all(math.isfinite(value) for value in (open_price, high, low, close))
                or min(open_price, high, low, close) <= 0
                or high < low
                or not low <= open_price <= high
                or not low <= close <= high
            ):
                issues.append(f"row={index}, timestamp={timestamp}: OHLC 無效")
                continue
            volume = float(values["volume"])
            if isinstance(values["volume"], bool) or not math.isfinite(volume) or volume < 0 or not volume.is_integer():
                raise DataProviderError(f"row={index}: 成交量必須是非負有限整數")
            bars.append(
                PriceBar(
                    date=datetime.fromtimestamp(timestamp, tz=timezone).date(),
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=int(volume),
                    adjusted_close=(
                        float(adjusted[index])
                        if index < len(adjusted) and adjusted[index] is not None
                        else None
                    ),
                )
            )
        if not bars:
            raise DataProviderError("沒有有效日線資料")
        bars.sort(key=lambda bar: bar.date)
        name = str(meta.get("longName") or meta.get("shortName") or symbol)
        return MarketData(symbol=symbol, name=name, bars=tuple(bars), source=self.source_name,
                          quality_issues=tuple(issues))
