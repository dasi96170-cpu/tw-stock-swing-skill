import unittest

from tw_stock_swing.indicators import atr, ema_series, macd, rsi, sma


class IndicatorTests(unittest.TestCase):
    def test_sma_uses_latest_period(self) -> None:
        self.assertEqual(sma([1, 2, 3, 4, 5], 3), 4)

    def test_ema_preserves_length_and_moves_toward_price(self) -> None:
        values = ema_series([10, 10, 10, 20], 3)
        self.assertEqual(len(values), 4)
        self.assertEqual(values[-1], 15)

    def test_rsi_flat_series_is_neutral(self) -> None:
        self.assertEqual(rsi([100.0] * 20), 50.0)

    def test_atr_accounts_for_gap(self) -> None:
        highs = [10.0, 12.0] + [12.0] * 14
        lows = [9.0, 11.0] + [11.0] * 14
        closes = [9.5, 11.5] + [11.5] * 14
        self.assertGreater(atr(highs, lows, closes, 14), 1.0)

    def test_macd_is_positive_for_rising_prices(self) -> None:
        line, signal, histogram = macd([float(value) for value in range(1, 81)])
        self.assertGreater(line, 0)
        self.assertGreaterEqual(histogram, -1e-6)
        self.assertGreater(line, signal - 1e-6)


if __name__ == "__main__":
    unittest.main()
