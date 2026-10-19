"""Read-only schema samples; no full announcement bodies are saved."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from tw_stock_swing.sources.official import _default_fetcher

TARGETS = {
    'twse_profile': 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
    'tpex_profile': 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O',
    'twse_announcements': 'https://openapi.twse.com.tw/v1/opendata/t187ap04_L',
    'tpex_announcements': 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O',
}


def inspect(item):
    name, url = item
    result = dict(connector=name, url=url, fetched_at=datetime.now(timezone.utc).isoformat())
    try:
        raw = _default_fetcher(url, 10)
        data = json.loads(raw)
        result.update(sha256=hashlib.sha256(raw).hexdigest(), rows=len(data), kind=type(data).__name__)
        sample = data[0] if isinstance(data, list) and data else {}
        result['fields'] = list(sample)
        result['sample'] = {k: v for k, v in sample.items() if any(x in k for x in
                            ('日期', '時間', '代號', '代碼', '產業', '股數', '資本', '出表', '上市', '上櫃'))}
    except Exception as exc:
        result['error'] = str(exc)
    return result


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=2) as pool:
        print(json.dumps(list(pool.map(inspect, TARGETS.items())), ensure_ascii=False, indent=2))
