from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Sequence

from .analysis import analyze_bars
from .formatting import format_analysis
from .models import AnalysisResult
from .providers import DataProviderError, YahooFinanceProvider


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="台股短線波段分析工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="分析單一股票")
    analyze_parser.add_argument("symbol", help="例如 2330、2330.TW 或 6488.TWO")
    analyze_parser.add_argument("--timeout", type=float, default=15.0, help="資料請求逾時秒數")
    analyze_parser.add_argument("--json", action="store_true", help="輸出 JSON")
    analyze_parser.set_defaults(handler=_run_analyze)

    scan_parser = subparsers.add_parser("scan", help="掃描並排序多檔股票")
    scan_parser.add_argument("symbols", nargs="*", help="指定代號；未指定時使用預設清單")
    scan_parser.add_argument("--symbols-file", help="自訂代號檔（未傳位置參數時使用）")
    scan_parser.add_argument("--limit", type=int, default=10, choices=range(1, 101), metavar="1-100")
    scan_parser.add_argument("--workers", type=int, default=4, choices=range(1, 9), metavar="1-8")
    scan_parser.add_argument("--timeout", type=float, default=15.0, help="每個資料請求逾時秒數")
    scan_parser.add_argument("--json", action="store_true", help="輸出 JSON")
    scan_parser.set_defaults(handler=_run_scan)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))
