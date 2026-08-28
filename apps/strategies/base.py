from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseStrategy(ABC):
    """Lớp trừu tượng định nghĩa chuẩn cho mọi chiến thuật giao dịch."""

    def __init__(self, name: str, timeframe: str = "M15"):
        self.name = name
        self.timeframe = timeframe

    @abstractmethod
    def analyze_market(self, symbol: str, price: float, atr: float, digits: int) -> Dict[str, Any]:
        """
        Phân tích thị trường và trả về dict chứa:
        - signal: 'BUY', 'SELL', 'HOLD'
        - entry_price, sl, tp1, tp2
        - confidence: 0-100%
        - rationale: Giải thích kỹ thuật
        """
        pass
