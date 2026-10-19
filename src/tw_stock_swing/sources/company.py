"""On-demand company profiles and official disclosure metadata, not news research.

Latest snapshots do not establish historical completeness or absence of events.
Announcement bodies and personal contact fields are never returned or persisted.
"""
from dataclasses import asdict, dataclass, replace
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import hashlib
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

from ..coverage import CoverageEntry
from .official import ConnectorError, Fetcher, _default_fetcher, parse_number

TAIPEI = ZoneInfo('Asia/Taipei')
PROFILE_FIELDS = {
    'identity.legal_name': ('公司名稱', 'CompanyName'),
    'identity.english_abbreviation': ('英文簡稱', 'Symbol'),
    'identity.industry_code': ('產業別', 'SecuritiesIndustryCode'),
    'identity.listing_date': ('上市日期', 'DateOfListing'),
    'identity.paid_in_capital': ('實收資本額', 'Paidin.Capital.NTDollars'),
    'identity.issue_shares_or_tdr_underlying': ('已發行普通股數或TDR原股發行股數', 'IssueShares'),
}
EVENT_FIELDS = (
    'material_events.announcements', 'material_events.headline',
    'material_events.published_at', 'material_events.event_date',
    'material_events.source_locator', 'material_events.revision_hash',
)


def _date(value: Any) -> date | None:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    text = str(value).strip()
    parts = re.fullmatch(r'(\d{2,4})[/-](\d{1,2})[/-](\d{1,2})', text)
    digits = re.fullmatch(r'\d{7,8}', text)
    if parts:
        year, month, day = map(int, parts.groups())
        roc = len(parts.group(1)) <= 3
    elif digits:
        year, month, day = int(text[:-4]), int(text[-4:-2]), int(text[-2:])
        roc = len(text) == 7
    else:
        return None
    if roc:
        year += 1911
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _published(day: Any, clock: Any) -> datetime | None:
    parsed_date = _date(day)
    value = str(clock).strip()
    if re.fullmatch(r'\d{1,2}:\d{2}:\d{2}', value):
        h, m, s = map(int, value.split(':'))
    elif re.fullmatch(r'\d{1,6}', value):
        value = value.zfill(6)
        h, m, s = int(value[:2]), int(value[2:4]), int(value[4:])
    else:
        return None
    try:
        return datetime(parsed_date.year, parsed_date.month, parsed_date.day, h, m, s, tzinfo=TAIPEI) if parsed_date else None
    except ValueError:
        return None


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _first(row, keys):
    return next((row[k] for k in keys if k in row), None)


@dataclass(frozen=True)
class Announcement:
    event_key: str
    revision_hash: str
    symbol: str
    market: str
    headline: str
    published_at: str | None
    event_date: str | None
    clause: str
    source_url: str
    source_record_key: str
    issues: tuple[str, ...]


@dataclass(frozen=True)
class CompanyResult:
    connector_id: str
    source_id: str
    endpoint: str
    symbol: str
    market: str
    fetched_at: datetime
    source_date: date | None
    fields: dict[str, Any]
    announcements: tuple[Announcement, ...]
    coverage: tuple[CoverageEntry, ...]
    issues: tuple[str, ...] = ()
    content_hash: str | None = None
    error: str | None = None
    evaluated_at: datetime | None = None

    def to_dict(self):
        return {
            'connector_id': self.connector_id, 'source_id': self.source_id,
            'endpoint': self.endpoint, 'symbol': self.symbol, 'market': self.market,
            'fetched_at': self.fetched_at.isoformat(),
            'source_date': self.source_date.isoformat() if self.source_date else None,
            'fields': self.fields, 'announcements': [asdict(item) for item in self.announcements],
            'coverage': [dict(field_id=item.field_id, status=item.status, reason=item.reason) for item in self.coverage],
            'issues': list(self.issues), 'content_hash': self.content_hash, 'error': self.error,
            'scope': 'latest_official_snapshot_only', 'absence_proven': False,
            'body_stored': False, 'parser_version': 'company-1',
            'evaluated_at': (self.evaluated_at or self.fetched_at).isoformat(),
        }


class CompanyConnector:
    def __init__(self, market: str, kind: str):
        if market not in {'TWSE', 'TPEx'} or kind not in {'profile', 'announcements'}:
            raise ValueError('未知市場或公司資料類型')
        self.market, self.kind = market, kind
        self.connector_id = f"{'twse' if market == 'TWSE' else 'tpex'}_{kind}"
        self.source_id = 'twse_openapi' if market == 'TWSE' else 'tpex_openapi'
        code = '03' if kind == 'profile' else '04'
        self.endpoint = (f'https://openapi.twse.com.tw/v1/opendata/t187ap{code}_L' if market == 'TWSE'
                         else f'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap{code}_O')
        self.contract_fields = frozenset(PROFILE_FIELDS if kind == 'profile' else EVENT_FIELDS)

    def failed(self, symbol: str, when: datetime, message: str) -> CompanyResult:
        return CompanyResult(self.connector_id, self.source_id, self.endpoint, symbol, self.market,
                             when, None, {}, (), tuple(CoverageEntry(field, 'failed', message, self.source_id, when)
                             for field in sorted(self.contract_fields)), error=message)

    def fetch_safe(self, symbol: str, *, timeout: float = 20, fetched_at: datetime | None = None,
                   fetcher: Fetcher = _default_fetcher, as_of: datetime | None = None) -> CompanyResult:
        when = fetched_at or datetime.now(timezone.utc)
        try:
            raw = fetcher(self.endpoint, timeout)
            result = self.parse(json.loads(raw.decode('utf-8-sig')), symbol=symbol, fetched_at=when, as_of=as_of)
            return replace(result, content_hash=hashlib.sha256(raw).hexdigest())
        except (ConnectorError, UnicodeError, json.JSONDecodeError) as exc:
            return self.failed(symbol, when, str(exc))

    def parse(self, payload: Any, *, symbol: str, fetched_at: datetime,
              as_of: datetime | None = None) -> CompanyResult:
        if fetched_at.tzinfo is None:
            raise ValueError('fetched_at 必須包含時區')
        cutoff = as_of or fetched_at
        if cutoff.tzinfo is None:
            raise ValueError('as_of 必須包含時區')
        if not re.fullmatch(r'\d{4,6}', symbol):
            raise ValueError('公司代號必須為4至6位數字；市場由連接器指定')
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise ConnectorError('公司資料必須是物件陣列')
        rows = []
        dates = set()
        issues = []
        date_missing = False
        for raw in payload:
            row = {str(k).strip(): v for k, v in raw.items()}
            if len(row) != len(raw):
                raise ConnectorError('欄位正規化後名稱重複')
            code = str(_first(row, ('公司代號', 'SecuritiesCompanyCode')) or '').strip()
            if not re.fullmatch(r'\d{4,6}', code):
                raise ConnectorError('來源含不可識別的公司代號，無法證明篩選完整')
            snapshot_date = _date(_first(row, ('出表日期', 'Date')))
            if snapshot_date:
                dates.add(snapshot_date)
            else:
                date_missing = True
            if code == symbol:
                rows.append(row)
        source_date = next(iter(dates)) if len(dates) == 1 and not date_missing else None
        today = cutoff.astimezone(TAIPEI).date()
        # Explicit calendar-day freshness budget; not a claim of exchange-calendar validation.
        time_status = ('unverified' if source_date is None or source_date > today else
                       'stale' if (today - source_date).days > 3 else 'available')
        if time_status != 'available':
            issues.append('snapshot_date_' + time_status)
        if not rows:
            issues.append('no_matching_rows_in_returned_snapshot_not_proof_of_absence')
        fields, events = {}, []
        statuses = {key: ('missing', 'no_matching_rows_in_snapshot') for key in self.contract_fields}
        if self.kind == 'profile':
            if len(rows) > 1:
                raise ConnectorError('同一公司有重複主檔')
            if rows:
                for key, aliases in PROFILE_FIELDS.items():
                    value = _first(rows[0], aliases)
                    if key.endswith('listing_date'):
                        day = _date(value)
                        value = day.isoformat() if day and day <= today else None
                    elif key.endswith(('paid_in_capital', 'issue_shares_or_tdr_underlying')):
                        value = parse_number(value)
                        if value is not None and (value < 0 or int(value) != value):
                            value = None
                    else:
                        value = (str(value).strip() if isinstance(value, (str, int))
                                 and not isinstance(value, bool) else None)
                    if value is not None and value != '':
                        fields[key] = value
                        statuses[key] = (time_status, 'profile_snapshot')
                    else:
                        statuses[key] = ('missing', 'field_missing_or_invalid')
        else:
            seen = set()
            for row in rows:
                headline_value = row.get('主旨')
                headline = ' '.join(headline_value.split()) if isinstance(headline_value, str) else ''
                publication = _published(row.get('發言日期'), row.get('發言時間'))
                fact_date = _date(row.get('事實發生日'))
                problems = []
                if not headline:
                    problems.append('missing_headline')
                if publication is None:
                    problems.append('unverified_publication_time')
                elif publication > min(cutoff, fetched_at):
                    problems.append('future_publication')
                if fact_date is None:
                    problems.append('unverified_event_date')
                elif fact_date > today:
                    problems.append('future_event_date')
                clause = str(row.get('符合條款') or '').strip()
                pub = publication.isoformat() if publication else None
                key = _hash([self.source_id, symbol, pub, headline, clause])
                # Include body in revision hash, but never retain the body itself.
                revision = _hash({k: v for k, v in row.items() if k not in {'出表日期', 'Date'}})
                if revision in seen:
                    continue
                seen.add(revision)
                events.append(Announcement(key, revision, symbol, self.market, headline, pub,
                    fact_date.isoformat() if fact_date else None, clause, self.endpoint,
                    f'{symbol}:{key}:{revision}', tuple(problems)))
            statuses['material_events.announcements'] = (time_status, 'returned_snapshot_only')
            if events:
                requirements = {'material_events.headline': lambda e: bool(e.headline),
                    'material_events.published_at': lambda e: e.published_at is not None and 'future_publication' not in e.issues,
                    'material_events.event_date': lambda e: e.event_date is not None and 'future_event_date' not in e.issues,
                    'material_events.source_locator': lambda e: True,
                    'material_events.revision_hash': lambda e: True}
                for field, predicate in requirements.items():
                    statuses[field] = ((time_status, 'all_returned_events') if all(predicate(e) for e in events)
                                       else ('unverified', 'one_or_more_event_fields_invalid'))
                if any(e.issues for e in events):
                    issues.append('one_or_more_events_require_verification')
            events.sort(key=lambda e: (e.published_at or '', e.event_key, e.revision_hash))
        coverage = tuple(CoverageEntry(key, *statuses[key], self.source_id, fetched_at) for key in sorted(statuses))
        return CompanyResult(self.connector_id, self.source_id, self.endpoint, symbol, self.market,
                             fetched_at, source_date, fields, tuple(events), coverage, tuple(issues), evaluated_at=cutoff)


COMPANY_CONNECTORS = {connector.connector_id: connector for market in ('TWSE', 'TPEx')
                      for kind in ('profile', 'announcements')
                      for connector in (CompanyConnector(market, kind),)}


def fetch_company_sources(symbol: str, market: str, *, timeout: float = 20,
                          connectors=None, as_of: datetime | None = None) -> tuple[CompanyResult, ...]:
    if market not in {'TWSE', 'TPEx'} or not re.fullmatch(r'\d{4,6}', symbol):
        raise ValueError('請指定正確市場與數字公司代號')
    if timeout <= 0:
        raise ValueError('timeout 必須大於0')
    registry = COMPANY_CONNECTORS if connectors is None else connectors
    prefix = 'twse' if market == 'TWSE' else 'tpex'
    names = [f'{prefix}_{kind}' for kind in ('profile', 'announcements')]
    def fetch(name):
        if name not in registry:
            return COMPANY_CONNECTORS[name].failed(symbol, datetime.now(timezone.utc), 'connector_not_configured')
        try:
            return registry[name].fetch_safe(symbol, timeout=timeout, as_of=as_of)
        except (ConnectorError, ValueError, OSError) as exc:
            return COMPANY_CONNECTORS[name].failed(symbol, datetime.now(timezone.utc), str(exc))
    with ThreadPoolExecutor(max_workers=2) as executor:
        return tuple(executor.map(fetch, names))
