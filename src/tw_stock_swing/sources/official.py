from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import gzip
import hashlib
from http.client import IncompleteRead
from io import BytesIO
import json
import math
import re
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from ..coverage import CoverageEntry


class ConnectorError(RuntimeError):
    """Official endpoint could not be fetched or parsed safely."""


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    field_id: str
    symbol: str
    value: float | int | str
    unit: str
    valid_time: datetime
    source_id: str
    source_record_key: str


@dataclass(frozen=True, slots=True)
class ConnectorResult:
    connector_id: str
    source_id: str
    endpoint: str
    fetched_at: datetime
    observations: tuple[NormalizedObservation, ...]
    coverage: tuple[CoverageEntry, ...]
    error: str | None = None
    content_hash: str | None = None
    valid_time_verified: bool = False


@dataclass(frozen=True, slots=True)
class FieldMapping:
    field_id: str
    unit: str
    aliases: tuple[str, ...]
    numeric: bool = True
    empty_is_zero: bool = False


Fetcher = Callable[[str, float], bytes]
MAX_RESPONSE_BYTES = 25 * 1024 * 1024
TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def _default_fetcher(url: str, timeout: float) -> bytes:
    if timeout <= 0:
        raise ConnectorError("timeout 必須大於 0")
    last_error: Exception | None = None
    for attempt in range(3):
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "User-Agent": "tw-stock-swing/0.1 (+research; on-demand)",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                final_url = response.geturl()
                if urlparse(final_url).hostname != urlparse(url).hostname:
                    raise ConnectorError("官方來源重新導向至未允許網域")
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise ConnectorError("官方來源回應的 Content-Length 無效") from exc
                    if declared_size > MAX_RESPONSE_BYTES:
                        raise ConnectorError(f"官方來源回應超過 {MAX_RESPONSE_BYTES} bytes")
                payload = response.read(MAX_RESPONSE_BYTES + 1)
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise ConnectorError(f"官方來源回應超過 {MAX_RESPONSE_BYTES} bytes")
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    with gzip.GzipFile(fileobj=BytesIO(payload)) as archive:
                        payload = archive.read(MAX_RESPONSE_BYTES + 1)
                    if len(payload) > MAX_RESPONSE_BYTES:
                        raise ConnectorError(f"官方來源解壓後超過 {MAX_RESPONSE_BYTES} bytes")
                return payload
        except HTTPError as exc:
            if exc.code not in TRANSIENT_HTTP_STATUSES:
                raise ConnectorError(f"官方來源請求失敗 {url}: HTTP {exc.code}") from exc
            last_error = exc
        except (URLError, TimeoutError, OSError, IncompleteRead) as exc:
            last_error = exc
        if attempt < 2:
            time.sleep(0.25 * (2 ** attempt))
    raise ConnectorError(f"官方來源請求失敗 {url}: {last_error}") from last_error


_EMPTY_VALUES = {"", "--", "---", "N/A", "null", "None", "除權息", "除息", "除權"}


def parse_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    text = str(value).strip().replace(",", "").replace("＋", "+").replace("－", "-")
    if text in _EMPTY_VALUES:
        return None
    text = re.sub(r"^[+]?", "", text)
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _first(record: Mapping[str, Any], aliases: Sequence[str]) -> Any:
    for alias in aliases:
        if alias in record:
            return record[alias]
    return None


def _valid_time(record: Mapping[str, Any], fallback: datetime) -> datetime:
    raw = _first(record, ("Date", "日期", "資料日期"))
    if raw is None:
        return fallback
    digits = re.sub(r"\D", "", str(raw))
    try:
        if len(digits) == 7:
            year, month, day = int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:])
        elif len(digits) == 8:
            year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:])
        else:
            return fallback
        return datetime(year, month, day, tzinfo=ZoneInfo("Asia/Taipei"))
    except ValueError:
        return fallback


def _has_valid_source_date(record: Mapping[str, Any]) -> bool:
    raw = _first(record, ("Date", "日期", "資料日期"))
    if raw is None:
        return False
    digits = re.sub(r"\D", "", str(raw))
    try:
        if len(digits) == 7:
            datetime(int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:]))
        elif len(digits) == 8:
            datetime(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
        else:
            return False
    except ValueError:
        return False
    return True


class OfficialTabularConnector:
    connector_id = ""
    source_id = ""
    endpoint = ""
    symbol_aliases: tuple[str, ...] = ()
    mappings: tuple[FieldMapping, ...] = ()

    @property
    def contract_fields(self) -> frozenset[str]:
        return frozenset(mapping.field_id for mapping in self.mappings)

    def fetch(
        self,
        symbol: str,
        *,
        timeout: float = 20.0,
        fetched_at: datetime | None = None,
        fetcher: Fetcher = _default_fetcher,
    ) -> ConnectorResult:
        when = fetched_at or datetime.now(timezone.utc)
        raw = fetcher(self.endpoint, timeout)
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectorError(f"{self.connector_id} 回傳非合法 JSON") from exc
        result = self.parse(payload, symbol=symbol, fetched_at=when)
        return replace(result, content_hash=hashlib.sha256(raw).hexdigest())

    def fetch_safe(
        self,
        symbol: str,
        *,
        timeout: float = 20.0,
        fetched_at: datetime | None = None,
        fetcher: Fetcher = _default_fetcher,
    ) -> ConnectorResult:
        """Fetch without losing the per-field contract when the source fails."""
        when = fetched_at or datetime.now(timezone.utc)
        try:
            return self.fetch(symbol, timeout=timeout, fetched_at=when, fetcher=fetcher)
        except ConnectorError as exc:
            message = str(exc)
            return ConnectorResult(
                self.connector_id,
                self.source_id,
                self.endpoint,
                when,
                (),
                tuple(
                    CoverageEntry(field_id, "failed", message, self.source_id, when)
                    for field_id in sorted(self.contract_fields)
                ),
                message,
            )

    def parse(self, payload: Any, *, symbol: str, fetched_at: datetime) -> ConnectorResult:
        if fetched_at.tzinfo is None:
            raise ValueError("fetched_at 必須包含時區")
        if isinstance(payload, dict):
            if payload.get("stat") not in (None, "OK"):
                raise ConnectorError(f"{self.connector_id} 官方來源狀態: {payload.get('stat')}")
            records = payload.get("data") or payload.get("aaData") or payload.get("records")
            fields = payload.get("fields")
            if isinstance(records, list) and records and isinstance(records[0], list):
                if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
                    raise ConnectorError(f"{self.connector_id} 表格式 JSON 缺少 fields")
                if len(set(fields)) != len(fields) or any(
                    not isinstance(row, list) or len(row) != len(fields) for row in records
                ):
                    raise ConnectorError(f"{self.connector_id} 表格欄名重複或列長度不一致")
                report_date = payload.get("date")
                records = [
                    {**dict(zip(fields, row)), **({"Date": report_date} if report_date else {})}
                    for row in records
                    if isinstance(row, list)
                ]
        else:
            records = payload
        if not isinstance(records, list):
            raise ConnectorError(f"{self.connector_id} JSON 頂層必須是陣列或含 data 陣列")
        if any(not isinstance(candidate, dict) for candidate in records):
            raise ConnectorError(f"{self.connector_id} 資料列必須是物件，不可混合格式")

        normalized_symbol = symbol.strip().upper().split(".", 1)[0]
        record: Mapping[str, Any] | None = None
        for candidate in records:
            if not isinstance(candidate, dict):
                continue
            candidate_symbol = str(_first(candidate, self.symbol_aliases) or "").strip().upper()
            if candidate_symbol == normalized_symbol:
                if record is not None:
                    raise ConnectorError(f"{self.connector_id} 同一股票有重複資料列")
                record = self.prepare_record(candidate)

        observations: list[NormalizedObservation] = []
        coverage: list[CoverageEntry] = []
        for mapping in self.mappings:
            raw_value = _first(record, mapping.aliases) if record else None
            value: int | float | str | None
            if mapping.numeric:
                if mapping.empty_is_zero and raw_value is not None and not str(raw_value).strip():
                    value = 0
                else:
                    value = parse_number(raw_value)
            else:
                value = str(raw_value).strip() if raw_value is not None and str(raw_value).strip() else None
            if value is None:
                reason = "symbol_not_found" if record is None else "field_absent_or_null"
                coverage.append(CoverageEntry(mapping.field_id, "missing", reason, self.source_id, fetched_at))
                continue
            observations.append(
                NormalizedObservation(
                    field_id=mapping.field_id,
                    symbol=normalized_symbol,
                    value=value,
                    unit=mapping.unit,
                    valid_time=_valid_time(record, fetched_at),
                    source_id=self.source_id,
                    source_record_key=normalized_symbol,
                )
            )
            coverage.append(CoverageEntry(mapping.field_id, "available", "official_payload", self.source_id, fetched_at))
        invalid = _invalid_observations(observations)
        observations = [item for item in observations if item.field_id not in invalid]
        coverage = [replace(item, status="conflicting", reason=invalid[item.field_id])
                    if item.field_id in invalid else item for item in coverage]
        return ConnectorResult(
            self.connector_id,
            self.source_id,
            self.endpoint,
            fetched_at,
            tuple(observations),
            tuple(coverage),
            valid_time_verified=_has_valid_source_date(record) if record else False,
        )

    def prepare_record(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        return record


def _invalid_observations(observations: list[NormalizedObservation]) -> dict[str, str]:
    """Quarantine invalid observations; retain one explicit coverage state each."""
    values = {item.field_id: item.value for item in observations}
    invalid: dict[str, str] = {}
    price_fields = {f"market.{key}" for key in ("open", "high", "low", "close")}
    for item in observations:
        value = item.value
        if not isinstance(value, (int, float)):
            continue
        # Net flows and price changes may be negative; turnover/balances may not.
        signed = item.field_id.endswith(".net") or item.field_id == "market.change"
        if not signed and value < 0:
            invalid[item.field_id] = "negative_unsigned_value"
        if item.field_id in price_fields and value <= 0:
            invalid[item.field_id] = "nonpositive_price"
        if item.unit in {"shares", "lots", "trades"} and int(value) != value:
            invalid[item.field_id] = "noninteger_count"
    if price_fields <= values.keys():
        low, high = values["market.low"], values["market.high"]
        if not (low <= values["market.open"] <= high and low <= values["market.close"] <= high):
            invalid.update({field: "inconsistent_ohlc" for field in price_fields})
    for group in ("foreign", "trust", "dealer"):
        keys = [f"institutional.{group}.{part}" for part in ("buy", "sell", "net")]
        if all(key in values for key in keys) and values[keys[0]] - values[keys[1]] != values[keys[2]]:
            invalid.update({key: "buy_minus_sell_disagrees_with_net" for key in keys})
    totals = {"market.volume", "market.value", "market.transactions"}
    if totals <= values.keys() and not (totals & invalid.keys()):
        positive = [values[key] > 0 for key in totals]
        if any(positive) and not all(positive):
            invalid.update({key: "inconsistent_volume_value_transactions" for key in totals})
    return invalid


MARKET_MAPPINGS = (
    FieldMapping("identity.symbol", "text", ("Code", "SecuritiesCompanyCode", "股票代號", "證券代號"), False),
    FieldMapping("identity.name", "text", ("Name", "CompanyName", "股票名稱", "證券名稱"), False),
    FieldMapping("market.open", "TWD/share", ("OpeningPrice", "Open", "開盤價")),
    FieldMapping("market.high", "TWD/share", ("HighestPrice", "High", "最高價")),
    FieldMapping("market.low", "TWD/share", ("LowestPrice", "Low", "最低價")),
    FieldMapping("market.close", "TWD/share", ("ClosingPrice", "Close", "收盤價")),
    FieldMapping("market.change", "TWD/share", ("Change", "漲跌價差", "漲跌")),
    FieldMapping("market.volume", "shares", ("TradeVolume", "TradingShares", "成交股數")),
    FieldMapping("market.value", "TWD", ("TradeValue", "TransactionAmount", "成交金額")),
    FieldMapping("market.transactions", "trades", ("Transaction", "TransactionNumber", "成交筆數")),
)

INSTITUTIONAL_MAPPINGS = (
    FieldMapping("institutional.foreign.buy", "shares", (
        "外陸資買進股數(不含外資自營商)",
        "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Buy",
        "ForeignInvestorsBuy", "ForeignInvestorsBuyAmount", "外資及陸資買進股數")),
    FieldMapping("institutional.foreign.sell", "shares", (
        "外陸資賣出股數(不含外資自營商)",
        " Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Sell",
        "ForeignInvestorsSell", "ForeignInvestorsSellAmount", "外資及陸資賣出股數")),
    FieldMapping("institutional.foreign.net", "shares", (
        "外陸資買賣超股數(不含外資自營商)",
        "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference",
        "ForeignInvestorsDifference", "ForeignInvestorsNet", "外資及陸資買賣超股數")),
    FieldMapping("institutional.trust.buy", "shares", (
        "投信買進股數", "SecuritiesInvestmentTrustCompanies-TotalBuy", "InvestmentTrustBuy", "InvestmentTrustBuyAmount")),
    FieldMapping("institutional.trust.sell", "shares", (
        "投信賣出股數", "SecuritiesInvestmentTrustCompanies-TotalSell", "InvestmentTrustSell", "InvestmentTrustSellAmount")),
    FieldMapping("institutional.trust.net", "shares", (
        "投信買賣超股數", "SecuritiesInvestmentTrustCompanies-Difference", "InvestmentTrustDifference", "InvestmentTrustNet")),
    FieldMapping("institutional.dealer.buy", "shares", (
        "自營商買進股數", "Dealers-TotalBuy", "DealerBuy", "DealerBuyAmount")),
    FieldMapping("institutional.dealer.sell", "shares", (
        "自營商賣出股數", "Dealers-TotalSell", "DealerSell", "DealerSellAmount")),
    FieldMapping("institutional.dealer.net", "shares", (
        "自營商買賣超股數", "Dealers-Difference", "DealerDifference", "DealerNet")),
)

MARGIN_MAPPINGS = (
    FieldMapping("margin.financing.previous_balance", "trading_units", ("融資前日餘額", "MarginPurchaseBalancePreviousDay"), empty_is_zero=True),
    FieldMapping("margin.financing.buy", "trading_units", ("融資買進", "MarginPurchase"), empty_is_zero=True),
    FieldMapping("margin.financing.sell", "trading_units", ("融資賣出", "MarginSales"), empty_is_zero=True),
    FieldMapping("margin.financing.cash_redemption", "trading_units", ("融資現金償還", "現金償還", "CashRedemption"), empty_is_zero=True),
    FieldMapping("margin.financing.balance", "trading_units", ("融資今日餘額", "融資餘額", "MarginPurchaseBalance"), empty_is_zero=True),
    FieldMapping("margin.short.previous_balance", "trading_units", ("融券前日餘額", "ShortSaleBalancePreviousDay"), empty_is_zero=True),
    FieldMapping("margin.short.sell", "trading_units", ("融券賣出", "ShortSale"), empty_is_zero=True),
    FieldMapping("margin.short.buy", "trading_units", ("融券買進", "ShortConvering"), empty_is_zero=True),
    FieldMapping("margin.short.stock_redemption", "trading_units", ("融券現券償還", "現券償還", "StockRedemption"), empty_is_zero=True),
    FieldMapping("margin.short.balance", "trading_units", ("融券今日餘額", "融券餘額", "ShortSaleBalance"), empty_is_zero=True),
)


class TwseMarketConnector(OfficialTabularConnector):
    connector_id = "twse_market"
    source_id = "twse_openapi"
    endpoint = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    symbol_aliases = ("Code", "股票代號", "證券代號")
    mappings = MARKET_MAPPINGS


class TpexMarketConnector(OfficialTabularConnector):
    connector_id = "tpex_market"
    source_id = "tpex_openapi"
    endpoint = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
    symbol_aliases = ("SecuritiesCompanyCode", "股票代號", "證券代號")
    mappings = MARKET_MAPPINGS


class TwseInstitutionalConnector(OfficialTabularConnector):
    connector_id = "twse_institutional"
    source_id = "twse_openapi"
    endpoint = "https://www.twse.com.tw/rwd/zh/fund/T86?response=json&selectType=ALLBUT0999"
    symbol_aliases = ("證券代號", "股票代號", "Code")
    mappings = INSTITUTIONAL_MAPPINGS

    def prepare_record(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        prepared = dict(record)
        for side in ("買進", "賣出"):
            own = parse_number(record.get(f"自營商{side}股數(自行買賣)"))
            hedge = parse_number(record.get(f"自營商{side}股數(避險)"))
            if own is not None and hedge is not None:
                prepared[f"自營商{side}股數"] = own + hedge
        return prepared


class TpexInstitutionalConnector(OfficialTabularConnector):
    connector_id = "tpex_institutional"
    source_id = "tpex_openapi"
    endpoint = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
    symbol_aliases = ("SecuritiesCompanyCode", "證券代號", "股票代號", "Code")
    mappings = INSTITUTIONAL_MAPPINGS


class TwseMarginConnector(OfficialTabularConnector):
    connector_id = "twse_margin"
    source_id = "twse_openapi"
    endpoint = "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"
    symbol_aliases = ("股票代號", "證券代號", "Code")
    mappings = MARGIN_MAPPINGS


class TpexMarginConnector(OfficialTabularConnector):
    connector_id = "tpex_margin"
    source_id = "tpex_openapi"
    endpoint = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance"
    symbol_aliases = ("SecuritiesCompanyCode", "股票代號", "證券代號", "Code")
    mappings = MARGIN_MAPPINGS


OFFICIAL_CONNECTORS = {
    connector.connector_id: connector
    for connector in (
        TwseMarketConnector(), TpexMarketConnector(),
        TwseInstitutionalConnector(), TpexInstitutionalConnector(),
        TwseMarginConnector(), TpexMarginConnector(),
    )
}
