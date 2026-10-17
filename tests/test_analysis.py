import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from tw_stock_swing.analysis import analyze_bars, round_to_tick
from tw_stock_swing.models import MarketData, PriceBar


def make_market_data(rising: bool = True, count: int = 90) -> MarketData:
    start = date.today() - timedelta(days=count)
    bars = []
    for index in range(count):
        direction = index if rising else count - index
        close = 50.0 + (direction * 0.5)
        volume = 2_000_000 if index == count - 1 else 1_000_000
        bars.append(
            PriceBar(
                date=start + timedelta(days=index),
                open=close - 0.2,
                high=close + 0.8,
                low=close - 0.8,
                close=close,
                volume=volume,
            )
        )
    return MarketData(
        symbol="2330.TW",
        name="測試股票",
        bars=tuple(bars),
        source="固定測試資料",
    )


class AnalysisTests(unittest.TestCase):
    def test_rising_series_scores_higher_than_falling_series(self) -> None:
        rising = analyze_bars(make_market_data(True))
        falling = analyze_bars(make_market_data(False))
        self.assertGreater(rising.score, falling.score)
        self.assertEqual(rising.verdict, "候選")
        self.assertEqual(falling.verdict, "避開")

    def test_trade_plan_has_ordered_prices(self) -> None:
        result = analyze_bars(make_market_data(True))
        self.assertLess(result.stop_loss, result.close)
        self.assertLessEqual(result.entry_low, result.entry_high)
        self.assertGreater(result.target_1, result.close)
        self.assertGreater(result.target_2, result.target_1)
        self.assertGreaterEqual(result.risk_reward_1, 1.9)

    def test_rejects_insufficient_history(self) -> None:
        with self.assertRaisesRegex(ValueError, "至少需要 60"):
            analyze_bars(make_market_data(count=59))

    def test_taiwan_tick_rounding(self) -> None:
        self.assertEqual(round_to_tick(48.03), 48.05)
        self.assertEqual(round_to_tick(105.2, "down"), 105.0)
        self.assertEqual(round_to_tick(1002.0, "up"), 1005.0)

    def test_intraday_daily_bar_is_excluded_until_market_close(self) -> None:
        as_of = datetime(2026, 10, 16, 10, 0, tzinfo=ZoneInfo("Asia/Taipei"))
        data = make_market_data(True)
        shift = as_of.date() - data.bars[-1].date
        shifted = MarketData(
            data.symbol,
            data.name,
            tuple(
                PriceBar(
                    date=bar.date + shift,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                )
                for bar in data.bars
            ),
            data.source,
        )
        result = analyze_bars(shifted, as_of=as_of)
        self.assertTrue(result.excluded_incomplete_bar)
        self.assertEqual(result.data_date, as_of.date() - timedelta(days=1))
        self.assertEqual(result.freshness_status, "current")


if __name__ == "__main__":
    unittest.main()
