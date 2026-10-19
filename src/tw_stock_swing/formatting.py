from __future__ import annotations

from .models import AnalysisResult
from .research import ResearchReport


def format_analysis(result: AnalysisResult) -> str:
    lines = [
        f"{result.symbol} {result.name}｜{result.verdict} {result.score}/100",
        f"資料：{result.data_date.isoformat()}｜{result.source}",
        f"收盤 {result.close:g}｜20 筆觀察間隔報酬 {result.change_20d_pct:.2f}%｜原股數量比 {result.volume_ratio:.2f}（非獨立買進訊號）",
        (
            f"SMA5/10/20/60：{result.sma5:.2f} / {result.sma10:.2f} / "
            f"{result.sma20:.2f} / {result.sma60:.2f}"
        ),
        f"RSI14 {result.rsi14:.2f}｜MACD {result.macd:.3f}｜ATR14 {result.atr14:.2f}",
    ]
    lines.append("交易計畫：未產生（僅技術篩選，未通過完整研究決策閘門）")
    lines.extend(f"初篩排除：{reason}" for reason in result.screening_blockers)
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
        (f"技術篩選：{analysis.verdict} {analysis.score}/100（尚未校準為勝率）" if analysis
         else f"技術篩選：不可用｜{report.analysis_error or '缺少有效日線'}"),
        f"研究時間：{report.generated_at.isoformat()}｜日線資料：{analysis.data_date.isoformat() if analysis else '不可用'}",
        f"市場路由：{report.market_resolution}" + (f"｜{report.routing_error}" if report.routing_error else ""),
        "",
        "技術與交易計畫",
        (
            f"收盤 {analysis.close:g}｜20 筆觀察間隔報酬 {analysis.change_20d_pct:.2f}%｜"
            f"原股數量比 {analysis.volume_ratio:.2f}（非獨立買進訊號）｜RSI14 {analysis.rsi14:.2f}"
        ) if analysis else "無技術分數與價位；以下僅列已取得的官方資料。",
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
        *(f"- {reason}" for reason in (analysis.reasons if analysis else ("未計算",))),
        "風險與限制",
        *(f"- {risk}" for risk in (analysis.risks if analysis else ("缺少有效技術資料，禁止形成交易計畫",))),
        *(f"- {warning}" for warning in report.warnings),
        *(f"- 決策阻擋：{blocker}" for blocker in report.decision_blockers),
        "",
        "來源健康",
        *(
            f"- {source.connector_id}: {source.status}｜{source.observations} 筆"
            + f"｜時間 {source.time_status}｜完整度 {source.completeness_status}"
            + (f"｜資料 {source.valid_times[-1].date().isoformat()}"
               if source.valid_times and source.valid_time_verified else "｜資料日期未確認")
            + (f"｜{source.error}" if source.error else "")
            for source in report.source_health
        ),
        "",
        "公司主檔與官方公告快照（不代表完整歷史或新聞研究）",
        *(
            f"- {source.connector_id}｜出表日 {source.source_date or '未確認'}｜來源 {source.endpoint}"
            + (f"｜失敗：{source.error}" if source.error else "")
            for source in report.company_sources
        ),
        *(
            f"- {source.market} {source.symbol} {key}: {value}"
            for source in report.company_sources for key, value in source.fields.items()
        ),
        *(
            f"- 公告 {event.market} {event.symbol} {event.published_at or '發布時間未確認'}｜{event.headline}"
            + (f"｜待核實：{', '.join(event.issues)}" if event.issues else "｜僅主旨，未分析全文")
            for source in report.company_sources for event in source.announcements
        ),
        "未查到公告不代表沒有事件；財報、營收、估值、外部新聞及其他重大事件缺口仍未完成。",
        "僅供研究與決策輔助；不是即時報價、投資建議或獲利保證。",
    ]
    return "\n".join(lines)
