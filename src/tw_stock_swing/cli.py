from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Sequence

from .analysis import analyze_bars
from .catalog import load_catalog, load_source_catalog
from .formatting import format_analysis, format_research
from .models import AnalysisResult
from .providers import DataProviderError, YahooFinanceProvider
from .research import research_stock
from .sources import ConnectorError, OFFICIAL_CONNECTORS
from .storage import ResearchDatabase


DEFAULT_WATCHLIST = Path(__file__).with_name("default_watchlist.txt")


def _load_symbols(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"無法讀取股票清單 {path}: {exc}") from exc
    symbols: list[str] = []
    for line in lines:
        value = line.split("#", 1)[0].strip()
        if value:
            symbols.append(value)
    return symbols


def _unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _error_payload(message: str) -> dict[str, object]:
    return {"status": "error", "error": message}


def _analyze_one(provider: YahooFinanceProvider, symbol: str) -> AnalysisResult:
    return analyze_bars(provider.fetch(symbol))


def _run_analyze(args: argparse.Namespace) -> int:
    provider = YahooFinanceProvider(timeout=args.timeout)
    try:
        result = _analyze_one(provider, args.symbol)
    except (ValueError, DataProviderError) as exc:
        payload = _error_payload(str(exc))
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"錯誤：{exc}")
        return 2
    if args.json:
        print(json.dumps({"status": "ok", "analysis": result.to_dict()}, ensure_ascii=False, indent=2))
    else:
        print(format_analysis(result))
    return 0


def _run_research(args: argparse.Namespace) -> int:
    provider = YahooFinanceProvider(timeout=args.timeout)
    try:
        report = research_stock(args.symbol, timeout=args.timeout, provider=provider)
    except (ValueError, DataProviderError) as exc:
        payload = _error_payload(str(exc))
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"錯誤：{exc}")
        return 2
    if args.json:
        print(
            json.dumps(
                {"status": report.status, "research": report.to_dict()},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(format_research(report))
    return 0


def _run_scan(args: argparse.Namespace) -> int:
    source_path = Path(args.symbols_file) if args.symbols_file else DEFAULT_WATCHLIST
    try:
        symbols = _unique([*args.symbols, *(_load_symbols(source_path) if not args.symbols else [])])
    except ValueError as exc:
        print(json.dumps(_error_payload(str(exc)), ensure_ascii=False, indent=2) if args.json else f"錯誤：{exc}")
        return 2
    if not symbols:
        print(json.dumps(_error_payload("股票清單是空的"), ensure_ascii=False, indent=2) if args.json else "錯誤：股票清單是空的")
        return 2

    provider = YahooFinanceProvider(timeout=args.timeout)
    results: list[AnalysisResult] = []
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(args.workers, len(symbols))) as executor:
        futures = {executor.submit(_analyze_one, provider, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results.append(future.result())
            except (ValueError, DataProviderError) as exc:
                errors[symbol] = str(exc)
    results.sort(key=lambda result: (result.score, result.volume_ratio), reverse=True)
    selected = results[: args.limit]

    if args.json:
        payload = {
            "status": "ok" if results else "error",
            "requested": len(symbols),
            "succeeded": len(results),
            "results": [result.to_dict() for result in selected],
            "errors": errors,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if selected:
            print(f"完成 {len(results)}/{len(symbols)} 檔；顯示前 {len(selected)} 檔")
            for index, result in enumerate(selected, start=1):
                warning = f"｜⚠ {result.freshness_warning}" if result.freshness_warning else ""
                print(
                    f"{index:>2}. {result.symbol:<10} {result.verdict:<2} "
                    f"{result.score:>3}/100｜收盤 {result.close:g}｜量比 {result.volume_ratio:.2f}{warning}"
                )
        if errors:
            print(f"另有 {len(errors)} 檔無法分析：", file=sys.stderr)
            for symbol, message in sorted(errors.items()):
                print(f"- {symbol}: {message}", file=sys.stderr)
    return 0 if results else 2


def _run_catalog(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    payload = {
        "version": catalog.version,
        "timezone": catalog.timezone,
        "field_count": len(catalog.fields),
        "fields": [
            {
                "id": field.id,
                "name": field.name,
                "category": field.category,
                "priority": field.priority,
                "kind": field.kind,
                "unit": field.unit,
                "freshness": field.freshness,
                "implementation": field.implementation,
                "sources": list(field.sources),
            }
            for field in catalog.fields
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _run_sources(args: argparse.Namespace) -> int:
    source_catalog = load_source_catalog()
    payload: dict[str, object] = {
        "checked_at": source_catalog["catalog"]["checked_at"],
        "validation_note": source_catalog["catalog"]["validation_note"],
        "sources": source_catalog["sources"],
    }
    exit_code = 0
    if args.live:
        results: dict[str, object] = {}
        for connector_id, connector in OFFICIAL_CONNECTORS.items():
            symbol = "2330" if connector.source_id == "twse_openapi" else "6488"
            try:
                result = connector.fetch_safe(symbol, timeout=args.timeout)
                if result.error:
                    results[connector_id] = {
                        "status": "failed",
                        "error": result.error,
                        "field_statuses": {item.field_id: item.status for item in result.coverage},
                    }
                    exit_code = 2
                    continue
                results[connector_id] = {
                    "status": "ok",
                    "observations": len(result.observations),
                    "missing": [item.field_id for item in result.coverage if item.status != "available"],
                    "fetched_at": result.fetched_at.isoformat(),
                }
            except ConnectorError as exc:
                results[connector_id] = {"status": "failed", "error": str(exc)}
                exit_code = 2
        payload["live"] = results
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code


def _run_init_db(args: argparse.Namespace) -> int:
    path = Path(args.database)
    try:
        with ResearchDatabase(path) as database:
            database.migrate()
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps(_error_payload(str(exc)), ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"status": "ok", "database": str(path), "schema_version": 1}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="台股短線波段分析工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="分析單一股票")
    analyze_parser.add_argument("symbol", help="例如 2330、2330.TW 或 6488.TWO")
    analyze_parser.add_argument("--timeout", type=float, default=15.0, help="資料請求逾時秒數")
    analyze_parser.add_argument("--json", action="store_true", help="輸出 JSON")
    analyze_parser.set_defaults(handler=_run_analyze)

    research_parser = subparsers.add_parser("research", help="整合官方資料研究單一股票")
    research_parser.add_argument("symbol", help="例如 2330、2330.TW 或 6488.TWO")
    research_parser.add_argument("--timeout", type=float, default=20.0, help="每個資料請求逾時秒數")
    research_parser.add_argument("--json", action="store_true", help="輸出完整 JSON 與 coverage")
    research_parser.set_defaults(handler=_run_research)

    scan_parser = subparsers.add_parser("scan", help="掃描並排序多檔股票")
    scan_parser.add_argument("symbols", nargs="*", help="指定代號；未指定時使用預設清單")
    scan_parser.add_argument("--symbols-file", help="自訂代號檔（未傳位置參數時使用）")
    scan_parser.add_argument("--limit", type=int, default=10, choices=range(1, 101), metavar="1-100")
    scan_parser.add_argument("--workers", type=int, default=4, choices=range(1, 9), metavar="1-8")
    scan_parser.add_argument("--timeout", type=float, default=15.0, help="每個資料請求逾時秒數")
    scan_parser.add_argument("--json", action="store_true", help="輸出 JSON")
    scan_parser.set_defaults(handler=_run_scan)

    catalog_parser = subparsers.add_parser("catalog", help="輸出機器可讀研究資料目錄")
    catalog_parser.set_defaults(handler=_run_catalog)

    sources_parser = subparsers.add_parser("sources", help="列出官方資料來源與驗證狀態")
    sources_parser.add_argument("--live", action="store_true", help="實際請求六個官方端點")
    sources_parser.add_argument("--timeout", type=float, default=20.0, help="資料請求逾時秒數")
    sources_parser.set_defaults(handler=_run_sources)

    database_parser = subparsers.add_parser("init-db", help="建立或升級 SQLite 研究資料庫")
    database_parser.add_argument("database", help="SQLite 檔案路徑")
    database_parser.set_defaults(handler=_run_init_db)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))
