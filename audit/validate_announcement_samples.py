"""Fetch each official feed once and validate an actually present company.

Output only schema/quality evidence; do not save complete bodies or raw feeds.
"""
from datetime import datetime, timezone
import hashlib
import json
from tw_stock_swing.sources.company import COMPANY_CONNECTORS
from tw_stock_swing.sources.official import _default_fetcher

results = []
for name in ('twse_announcements', 'tpex_announcements'):
    connector = COMPANY_CONNECTORS[name]
    result = {'connector_id': name, 'endpoint': connector.endpoint}
    try:
        raw = _default_fetcher(connector.endpoint, 8)
        payload = json.loads(raw)
        if not isinstance(payload, list) or not payload:
            result['status'] = 'no_sample_available'
        else:
            row = payload[0]
            symbol = str(row.get('公司代號') or row.get('SecuritiesCompanyCode'))
            fetched = datetime.now(timezone.utc)
            parsed = connector.parse(payload, symbol=symbol, fetched_at=fetched)
            result.update(symbol=symbol, fetched_at=fetched.isoformat(),
                          content_hash=hashlib.sha256(raw).hexdigest(),
                          source_date=str(parsed.source_date), event_count=len(parsed.announcements),
                          status='parsed', coverage={c.field_id: c.status for c in parsed.coverage},
                          issues=list(parsed.issues), event_issues=[e.issues for e in parsed.announcements])
    except Exception as exc:
        result.update(status='failed', error=str(exc))
    results.append(result)
print(json.dumps(results, ensure_ascii=False, indent=2))
raise SystemExit(2 if any(r['status'] == 'failed' for r in results) else 0)
