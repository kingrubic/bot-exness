import os
import logging
from decimal import Decimal
from django.utils import timezone

logger = logging.getLogger(__name__)

# Try importing MetaTrader5 if available (Windows / MT5 environment)
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False


class ExnessMT5Connector:
    """
    Bridge kết nối trực tiếp với MetaTrader 5 của sàn Exness.
    Tự động nhận diện môi trường:
    - Nếu có MT5 trên Windows/VPS -> Kết nối Live đặt lệnh thực tế.
    - Nếu trên Linux/Sandbox -> Chạy qua Mock Simulation Engine mượt mà.
    """

    def __init__(self, login: int = None, password: str = None, server: str = "Exness-MT5Real"):
        self.login = login
        self.password = password
        self.server = server
        self.is_connected = False

    def connect(self) -> bool:
        """Khởi tạo kết nối tới MT5 Terminal."""
        if not MT5_AVAILABLE:
            logger.info("MetaTrader5 package không khả dụng trên hệ điều hành này. Sử dụng Simulation Mode.")
            self.is_connected = True
            return True

        if not mt5.initialize():
            logger.error(f"Khởi tạo MT5 thất bại: {mt5.last_error()}")
            return False

        if self.login and self.password:
            authorized = mt5.login(
                login=int(self.login),
                password=self.password,
                server=self.server
            )
            if not authorized:
                logger.error(f"Đăng nhập Exness MT5 thất bại: {mt5.last_error()}")
                return False

        self.is_connected = True
        logger.info(f"✅ Đã kết nối thành công tới Exness MT5 #{self.login} ({self.server})")
        return True

    def disconnect(self):
        """Ngắt kết nối MT5."""
        if MT5_AVAILABLE and self.is_connected:
            mt5.shutdown()
            self.is_connected = False

    def get_symbol_price(self, symbol: str) -> dict:
        """Lấy giá Bid/Ask/Spread mới nhất của cặp giao dịch."""
        if not MT5_AVAILABLE or not self.is_connected:
            return None

        # Resolve Exness symbol suffix if needed (XAUUSDm, XAUUSD_i)
        symbol_info = mt5.symbol_info_tick(symbol)
        if not symbol_info:
            # Try with 'm' suffix (Exness Standard account)
            symbol_info = mt5.symbol_info_tick(f"{symbol}m")

        if symbol_info:
            return {
                'bid': symbol_info.bid,
                'ask': symbol_info.ask,
                'last': symbol_info.last,
                'spread_pips': round((symbol_info.ask - symbol_info.bid) / (symbol_info.point * 10), 1),
                'time': symbol_info.time
            }
        return None

    def send_order(self, symbol: str, order_type: str, volume: float, price: float, sl: float, tp: float, comment: str = "AutoBot") -> dict:
        """Gửi lệnh Mua/Bán lên sàn Exness MT5."""
        if not MT5_AVAILABLE or not self.is_connected:
            # Simulated order return
            import random
            return {
                'success': True,
                'ticket': f"{random.randint(10000000, 99999999)}",
                'price': price,
                'volume': volume,
                'comment': 'Simulated Execution'
            }

        mt5_order_type = mt5.ORDER_TYPE_BUY if order_type == 'BUY' else mt5.ORDER_TYPE_SELL
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": mt5_order_type,
            "price": float(price),
            "sl": float(sl),
            "tp": float(tp),
            "deviation": 20,
            "magic": 8882026,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Gửi lệnh thất bại: retcode={result.retcode}, error={mt5.last_error()}")
            return {'success': False, 'error': f"Retcode: {result.retcode}"}

        return {
            'success': True,
            'ticket': str(result.order),
            'price': result.price,
            'volume': result.volume,
            'comment': comment
        }

    def close_order(self, ticket: int, symbol: str, order_type: str, volume: float) -> bool:
        """Đóng lệnh MT5."""
        if not MT5_AVAILABLE or not self.is_connected:
            return True

        close_type = mt5.ORDER_TYPE_SELL if order_type == 'BUY' else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return False

        price = tick.bid if order_type == 'BUY' else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": int(ticket),
            "symbol": symbol,
            "volume": float(volume),
            "type": close_type,
            "price": float(price),
            "deviation": 20,
            "magic": 8882026,
            "comment": "Bot Close Position",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        return result.retcode == mt5.TRADE_RETCODE_DONE
