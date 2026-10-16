"""Taiwan stock swing analysis engine."""

from .analysis import analyze_bars
from .models import AnalysisResult, PriceBar
from .providers import YahooFinanceProvider

__all__ = ["AnalysisResult", "PriceBar", "YahooFinanceProvider", "analyze_bars"]
__version__ = "0.1.0"
