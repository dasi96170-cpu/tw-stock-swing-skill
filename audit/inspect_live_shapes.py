"""Supplementary read-only diagnostics, not a connector implementation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from urllib.request import Request, urlopen
from tw_stock_swing.sources import OFFICIAL_CONNECTORS

targets = {
    'tpex_market_diagnostic': OFFICIAL_CONNECTORS['tpex_market'].endpoint,
    'twse_margin_diagnostic': OFFICIAL_CONNECTORS['twse_margin'].endpoint,
    'twse_schema_discovery': 'https://openapi.twse.com.tw/v1/swagger.json',
    'tpex_schema_discovery': 'https://www.tpex.org.tw/openapi/swagger.json',
}


def inspect(item):
    name, url = item
    result = {'name': name, 'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat()}
    try:
        with urlopen(Request(url, headers={'Accept': 'application/json',
                          'User-Agent': 'tw-stock-swing/0.1 (+research; on-demand)'}), timeout=10) as response:
            raw = response.read(5000001)
            result.update(http_status=response.status, content_type=response.headers.get('Content-Type'),
                          bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        if len(raw) > 5000000:
            result['diagnostic'] = 'oversized; parsing skipped'
            return result
        try:
            payload = json.loads(raw.decode('utf-8-sig'))
        except (UnicodeError, ValueError):
            result['diagnostic'] = 'non_json'
            result['body_prefix'] = raw[:180].decode('utf-8', errors='replace')
            return result
        if isinstance(payload, list):
            result['row_count'] = len(payload)
            result['first_record_keys'] = list(payload[0]) if payload and isinstance(payload[0], dict) else []
            result['first_record_date_fields'] = {k: v for k, v in payload[0].items()
                if 'date' in k.lower() or '日期' in k} if payload and isinstance(payload[0], dict) else {}
        elif isinstance(payload, dict):
            result['top_level_keys'] = list(payload)
            paths = payload.get('paths', {})
            result['relevant_paths'] = {k: [entry.get('summary', '') for entry in v.values() if isinstance(entry, dict)]
                for k, v in paths.items() if any(token in str(v) + k for token in
                ('營收', '財務', '重大', '股利', '注意', '處置', '本益', '基本資料', '借券', '當沖'))}
        else:
            result['diagnostic'] = 'unexpected_shape'
    except Exception as exc:
        result['error'] = str(exc)
    return result


with ThreadPoolExecutor(max_workers=4) as pool:
    print(json.dumps(list(pool.map(inspect, targets.items())), ensure_ascii=False, indent=2))
