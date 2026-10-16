from __future__ import annotations

from .models import AnalysisResult


def format_analysis(result: AnalysisResult) -> str:
    lines = [
        f"{result.symbol} {result.name}｜{result.verdict} {result.score}/100",
        f"資料：{result.data_date.isoformat()}｜{result.source}",
        f"收盤 {result.close:g}｜20 日報酬 {result.change_20d_pct:.2f}%｜量比 {result.volume_ratio:.2f}",
        (
            f"SMA5/10/20/60：{result.sma5:.2f} / {result.sma10:.2f} / "
            f"{result.sma20:.2f} / {result.sma60:.2f}"
        ),
        f"RSI14 {result.rsi14:.2f}｜MACD {result.macd:.3f}｜ATR14 {result.atr14:.2f}",
        (
            f"參考計畫：入場 {result.entry_low:g}–{result.entry_high:g}｜"
            f"停損 {result.stop_loss:g}｜目標 {result.target_1:g} / {result.target_2:g}｜"
            f"第一目標風報比 {result.risk_reward_1:.2f}"
        ),
    ]
    if result.freshness_warning:
        lines.append(f"資料警告：{result.freshness_warning}")
    lines.append("理由：")
    lines.extend(f"- {reason}" for reason in result.reasons)
    lines.append("風險：")
    lines.extend(f"- {risk}" for risk in result.risks)
    lines.append("僅供研究與決策輔助；價位由日線與 ATR 推算，並非即時委託建議。")
    return "\n".join(lines)
