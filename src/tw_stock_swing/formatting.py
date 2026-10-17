from __future__ import annotations

from .models import AnalysisResult
from .research import ResearchReport


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
    ]
    if result.freshness_status == "current":
        lines.append(
            f"技術面參考價位：入場 {result.entry_low:g}–{result.entry_high:g}｜"
            f"停損 {result.stop_loss:g}｜目標 {result.target_1:g} / {result.target_2:g}｜"
            f"第一目標風報比 {result.risk_reward_1:.2f}"
        )
    else:
        lines.append("技術面參考價位：未顯示（完整日線資料時間未通過）")
    if result.freshness_warning:
        lines.append(f"資料警告：{result.freshness_warning}")
    lines.append("理由：")
    lines.extend(f"- {reason}" for reason in result.reasons)
    lines.append("風險：")
    lines.extend(f"- {risk}" for risk in result.risks)
    lines.append("僅供研究與決策輔助；價位由日線與 ATR 推算，並非即時委託建議。")
    return "\n".join(lines)


def _number(value: object, suffix: str = "", *, signed: bool = False) -> str:
    if isinstance(value, int):
        return f"{value:+,}{suffix}" if signed else f"{value:,}{suffix}"
    if isinstance(value, float):
        return f"{value:+,.2f}{suffix}" if signed else f"{value:,.2f}{suffix}"
    return f"{value}{suffix}"


def format_research(report: ResearchReport) -> str:
    analysis = report.analysis
    values = report.observations_by_id

    def value(field_id: str, suffix: str = "", *, signed: bool = False) -> str:
        observation = values.get(field_id)
        return _number(observation.value, suffix, signed=signed) if observation else "缺少"

    decision_label = "可形成決策" if report.decision_eligible else "禁止形成正式交易決策"
    lines = [
        f"{report.symbol} {report.name}｜{report.market}｜研究狀態 {report.status}｜{decision_label}",
        f"技術篩選：{analysis.verdict} {analysis.score}/100（尚未校準為勝率）",
        f"研究時間：{report.generated_at.isoformat()}｜日線資料：{analysis.data_date.isoformat()}",
        "",
        "技術與交易計畫",
        (
            f"收盤 {analysis.close:g}｜20 日報酬 {analysis.change_20d_pct:.2f}%｜"
            f"量比 {analysis.volume_ratio:.2f}｜RSI14 {analysis.rsi14:.2f}"
        ),
        *(
            [
                f"參考入場 {analysis.entry_low:g}–{analysis.entry_high:g}｜停損 {analysis.stop_loss:g}｜"
                f"目標 {analysis.target_1:g} / {analysis.target_2:g}｜風報比 {analysis.risk_reward_1:.2f}"
            ]
            if report.decision_eligible
            else ["正式交易計畫：未產生（關鍵 P0 資料、時效或一致性檢查未通過）"]
        ),
        "",
        "官方行情",
        (
            f"開／高／低／收：{value('market.open')} / {value('market.high')} / "
            f"{value('market.low')} / {value('market.close')}｜漲跌 {value('market.change')}"
        ),
        f"成交量 {value('market.volume', ' 股')}｜成交金額 {value('market.value', ' 元')}",
        "",
        "三大法人（單日淨額）",
        (
            f"外資 {value('institutional.foreign.net', ' 股', signed=True)}｜"
            f"投信 {value('institutional.trust.net', ' 股', signed=True)}｜"
            f"自營商 {value('institutional.dealer.net', ' 股', signed=True)}"
        ),
        "",
        "融資融券",
        (
            f"融資餘額 {value('margin.financing.balance', ' 張')}｜"
            f"前日 {value('margin.financing.previous_balance', ' 張')}"
        ),
        (
            f"融券餘額 {value('margin.short.balance', ' 張')}｜"
            f"前日 {value('margin.short.previous_balance', ' 張')}"
        ),
        "",
        "籌碼觀察",
        *(f"- {finding}" for finding in (report.official_findings or ("官方籌碼資料不足",))),
        "",
        "技術面理由",
        *(f"- {reason}" for reason in analysis.reasons),
        "風險與限制",
        *(f"- {risk}" for risk in analysis.risks),
        *(f"- {warning}" for warning in report.warnings),
        *(f"- 決策阻擋：{blocker}" for blocker in report.decision_blockers),
        "",
        "來源健康",
        *(
            f"- {source.connector_id}: {source.status}｜{source.observations} 筆"
            + (f"｜資料 {source.valid_times[-1].date().isoformat()}" if source.valid_times else "")
            + (f"｜{source.error}" if source.error else "")
            for source in report.source_health
        ),
        "",
        "本版只涵蓋行情、技術、法人與融資券；財報、營收、估值、新聞及重大事件缺口已列出。",
        "僅供研究與決策輔助；不是即時報價、投資建議或獲利保證。",
    ]
    return "\n".join(lines)
