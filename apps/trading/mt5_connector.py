import os
import logging
import requests
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from django.utils import timezone

logger = logging.getLogger(__name__)

# Try importing MetaTrader5 if available natively (Windows / MT5 environment)
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

BRIDGE_URL = os.environ.get("MT5_BRIDGE_URL", "http://127.0.0.1:9999")


class ExnessMT5Connector:
    """
    Bridge kết nối tài khoản Exness MT5:
    - Hỗ trợ Native MT5 trên Windows / Windows VPS.
    - Hỗ trợ MT5 Wine Bridge trên Linux / Ubuntu (Chạy trực tiếp MT5 Terminal qua Wine).
    - Tự động fallback Standby nếu chưa bật Terminal.
    """

    _bridge_available = None
    _last_bridge_check = 0

    @classmethod
    def is_bridge_reachable(cls) -> bool:
        """Kiểm tra nhanh (40ms) xem MT5 Terminal/Bridge có đang lắng nghe cổng không để tránh nghẽn I/O."""
        if MT5_AVAILABLE:
            return True
        import time, socket
        now = time.time()
        if cls._bridge_available is not None and (now - cls._last_bridge_check) < 5.0:
            return cls._bridge_available

        cls._last_bridge_check = now
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.04)
            res = s.connect_ex(('127.0.0.1', 9999))
            s.close()
            cls._bridge_available = (res == 0)
        except Exception:
            cls._bridge_available = False
        return cls._bridge_available

    def __init__(self, login: int = None, password: str = None, server: str = "Exness-MT5Real"):
        self.login = login
        self.password = password
        self.server = server
        self.is_connected = False
        self.bridge_mode = False

    @classmethod
    def test_connection(cls, login, password, server, account_type='REAL') -> tuple[bool, str, dict]:
        """
        Kiểm tra kết nối trực tiếp tới tài khoản Exness MT5.
        """
        if not login or not str(login).strip():
            return False, "Số tài khoản MT5 không được để trống.", {}
        if not password or not str(password).strip():
            return False, "Mật khẩu MT5 không được để trống.", {}
        if not server or not str(server).strip():
            return False, "Máy chủ Exness không được để trống.", {}

        login_str = str(login).replace('#', '').strip()
        if not login_str.isdigit():
            return False, f"Số tài khoản MT5 '{login_str}' không hợp lệ (phải là dãy chữ số).", {}

        server_clean = str(server).strip()

        # 1. Thử kết nối Native MetaTrader5 (Chạy trên Windows / Windows VPS)
        if MT5_AVAILABLE:
            try:
                if mt5.initialize():
                    auth = mt5.login(login=int(login_str), password=password, server=server_clean)
                    if auth:
                        acc = mt5.account_info()
                        if acc:
                            return True, f"Kết nối sàn Exness thành công #{acc.login} ({acc.server}) - Số dư thực tế: ${acc.balance:,.2f}", {
                                'login': acc.login,
                                'server': acc.server,
                                'balance': float(acc.balance),
                                'equity': float(acc.equity),
                                'leverage': acc.leverage,
                                'currency': acc.currency,
                                'mode': 'EXNESS_LIVE_MT5'
                            }
                    else:
                        err = mt5.last_error()
                        return False, f"Đăng nhập Exness thất bại: Mã lỗi MT5 {err}", {}
            except Exception as e:
                logger.debug(f"Native MT5 login error: {e}")

        # 2. Thử kết nối qua MT5 Wine Bridge (Chạy trên Linux với MT5 Terminal trong Wine)
        try:
            resp = requests.post(f"{BRIDGE_URL}/login", json={
                'login': int(login_str),
                'password': password,
                'server': server_clean
            }, timeout=25)
            res_data = resp.json()
            if resp.status_code == 200 and res_data.get('success'):
                return True, res_data.get('message', 'Kết nối thành công tới sàn Exness MT5'), res_data.get('account_info', {})
            elif res_data.get('error'):
                return False, f"Không thể đăng nhập máy chủ Exness: {res_data.get('error')}", {}
        except Exception as be:
            logger.debug(f"MT5 Wine Bridge error: {be}")

        if account_type == 'REAL':
            return False, "Không thể kết nối tới phần mềm Exness MT5 Terminal. Vui lòng mở phần mềm MT5 và đăng nhập tài khoản trước khi kết nối.", {}

        # 3. Chế độ Standby / Sandbox Simulation (Chỉ khi không thể kết nối MT5 Terminal thật)
        return False, "Chưa tìm thấy kết nối MT5 Terminal đang hoạt động. Vui lòng mở phần mềm Exness MT5 trên máy để đồng bộ số dư và lệnh thực tế.", {}

    def connect(self) -> bool:
        """Khởi tạo kết nối tới MT5 Terminal (Native hoặc Wine Bridge)."""
        if not self.login:
            return False

        login_clean = int(str(self.login).replace('#', '').strip()) if str(self.login).replace('#', '').strip().isdigit() else 0
        if not login_clean:
            return False

        # 1. Native Windows
        if MT5_AVAILABLE:
            try:
                acc = mt5.account_info()
                if acc and acc.login == login_clean:
                    self.is_connected = True
                    return True
                if mt5.initialize():
                    if self.login and self.password:
                        if mt5.login(login=login_clean, password=self.password, server=self.server):
                            self.is_connected = True
                            return True
            except Exception:
                pass

        # 2. Wine Bridge on Linux
        if not MT5_AVAILABLE and not self.is_bridge_reachable():
            self.is_connected = False
            return False

        try:
            # 2.1 Quick check if already logged into the requested account
            try:
                chk = requests.get(f"{BRIDGE_URL}/account_info", timeout=0.8)
                if chk.status_code == 200 and chk.json().get('success'):
                    acc = chk.json().get('account_info', {})
                    if acc.get('login') == login_clean:
                        self.is_connected = True
                        self.bridge_mode = True
                        return True
            except Exception:
                pass

            # 2.2 Perform login request
            resp = requests.post(f"{BRIDGE_URL}/login", json={
                'login': login_clean,
                'password': self.password,
                'server': self.server
            }, timeout=3.0)
            if resp.status_code == 200 and resp.json().get('success'):
                self.is_connected = True
                self.bridge_mode = True
                return True
        except Exception:
            pass

        self.is_connected = False
        return False

    def disconnect(self):
        """Ngắt kết nối MT5."""
        if MT5_AVAILABLE and self.is_connected and not self.bridge_mode:
            mt5.shutdown()
        self.is_connected = False

    def get_symbol_price(self, symbol: str) -> dict:
        """Lấy giá Bid/Ask/Spread mới nhất của cặp giao dịch."""
        if MT5_AVAILABLE and self.is_connected and not self.bridge_mode:
            symbol_info = mt5.symbol_info_tick(symbol) or mt5.symbol_info_tick(f"{symbol}m")
            if symbol_info:
                return {
                    'bid': symbol_info.bid,
                    'ask': symbol_info.ask,
                    'last': symbol_info.last,
                    'spread_pips': round((symbol_info.ask - symbol_info.bid) / (symbol_info.point * 10), 1),
                    'time': symbol_info.time
                }

        if self.bridge_mode or not MT5_AVAILABLE:
            try:
                resp = requests.post(f"{BRIDGE_URL}/symbol_price", json={'symbol': symbol}, timeout=4)
                if resp.status_code == 200 and resp.json().get('success'):
                    return resp.json().get('price')
            except Exception:
                pass

        return None

    def send_order(self, symbol: str, order_type: str, volume: float, price: float = 0.0, sl: float = 0.0, tp: float = 0.0, comment: str = "AutoBot") -> dict:
        """Gửi lệnh Mua/Bán lên sàn Exness MT5."""
        if not self.is_connected:
            self.connect()

        if MT5_AVAILABLE and not self.bridge_mode and self.is_connected:
            mt5_order_type = mt5.ORDER_TYPE_BUY if order_type == 'BUY' else mt5.ORDER_TYPE_SELL
            
            candidates = [symbol, f"{symbol}m", f"{symbol}_i", f"{symbol}c", symbol.upper()]
            sym_name = symbol
            sym_info = None
            for cand in candidates:
                try:
                    mt5.symbol_select(cand, True)
                    info = mt5.symbol_info(cand)
                    if info:
                        sym_name = cand
                        sym_info = info
                        break
                except Exception:
                    pass

            modes = []
            if sym_info and sym_info.filling_mode:
                if sym_info.filling_mode & 1: modes.append(mt5.ORDER_FILLING_FOK)
                if sym_info.filling_mode & 2: modes.append(mt5.ORDER_FILLING_IOC)
                if sym_info.filling_mode & 4: modes.append(mt5.ORDER_FILLING_RETURN)
            if not modes:
                modes = [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]

            last_res = None
            for f_mode in modes:
                tick = mt5.symbol_info_tick(sym_name)
                current_mkt_price = float(tick.ask if order_type == 'BUY' else tick.bid) if tick else float(price)

                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": sym_name,
                    "volume": float(volume),
                    "type": mt5_order_type,
                    "price": current_mkt_price,
                    "sl": float(sl) if sl else 0.0,
                    "tp": float(tp) if tp else 0.0,
                    "deviation": 100,
                    "magic": 8882026,
                    "comment": comment,
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": f_mode,
                }

                result = mt5.order_send(request)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    return {
                        'success': True,
                        'ticket': str(result.order),
                        'deal': str(result.deal),
                        'price': float(result.price),
                        'volume': float(result.volume),
                        'comment': result.comment or comment
                    }
                last_res = result

            err_msg = last_res.comment if last_res else str(mt5.last_error())
            return {'success': False, 'error': f"Lỗi MT5 mở lệnh: {err_msg}"}

        if self.bridge_mode or not MT5_AVAILABLE:
            try:
                resp = requests.post(f"{BRIDGE_URL}/order_send", json={
                    'symbol': symbol,
                    'order_type': order_type,
                    'volume': volume,
                    'price': price,
                    'sl': sl,
                    'tp': tp,
                    'comment': comment
                }, timeout=10)
                if resp.status_code == 200 and resp.json().get('success'):
                    return resp.json()
                elif resp.json().get('error'):
                    return {'success': False, 'error': resp.json().get('error')}
            except Exception as e:
                return {'success': False, 'error': f'Lỗi kết nối Bridge: {e}'}

        return {'success': False, 'error': 'Chưa kết nối sàn Exness MT5'}

    def close_order(self, ticket: int, symbol: str, order_type: str, volume: float) -> tuple[bool, str, dict]:
        """
        Đóng lệnh trên Exness MT5.
        Trả về (success: bool, message: str, data: dict).
        """
        if not self.is_connected:
            self.connect()

        ticket_clean = int(str(ticket).replace('#', '').strip()) if str(ticket).replace('#', '').strip().isdigit() else 0

        if MT5_AVAILABLE and not self.bridge_mode and self.is_connected:
            pos = None
            if ticket_clean > 0:
                all_pos = mt5.positions_get()
                if all_pos:
                    for p in all_pos:
                        if int(p.ticket) == ticket_clean:
                            pos = p
                            break

            if pos:
                real_symbol = pos.symbol
                real_vol = float(pos.volume)
                close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
            else:
                if ticket_clean > 0:
                    return True, f"Vị thế #{ticket_clean} đã đóng trước đó trên sàn MT5", {'already_closed': True}
                real_symbol = symbol
                real_vol = float(volume)
                close_type = mt5.ORDER_TYPE_SELL if order_type == 'BUY' else mt5.ORDER_TYPE_BUY

            mt5.symbol_select(real_symbol, True)
            sym_info = mt5.symbol_info(real_symbol)

            modes = []
            if sym_info and sym_info.filling_mode:
                if sym_info.filling_mode & 1: modes.append(mt5.ORDER_FILLING_FOK)
                if sym_info.filling_mode & 2: modes.append(mt5.ORDER_FILLING_IOC)
                if sym_info.filling_mode & 4: modes.append(mt5.ORDER_FILLING_RETURN)
            if not modes:
                modes = [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]

            last_res = None
            for f_mode in modes:
                tick = mt5.symbol_info_tick(real_symbol)
                if not tick:
                    continue
                price = float(tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask)
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "position": ticket_clean,
                    "symbol": real_symbol,
                    "volume": real_vol,
                    "type": close_type,
                    "price": price,
                    "deviation": 100,
                    "magic": 8882026,
                    "comment": "Bot Close",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": f_mode,
                }
                res = mt5.order_send(req)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    return True, f"Đã đóng thành công lệnh #{ticket_clean} trên Exness MT5", {'deal': str(res.deal), 'price': float(res.price)}
                last_res = res

            err_msg = last_res.comment if last_res else str(mt5.last_error())
            return False, f"MT5 từ chối đóng lệnh: {err_msg}", {}

        if self.bridge_mode or not MT5_AVAILABLE:
            try:
                resp = requests.post(f"{BRIDGE_URL}/close_order", json={
                    'ticket': ticket_clean,
                    'symbol': symbol,
                    'order_type': order_type,
                    'volume': volume
                }, timeout=10)
                res_data = resp.json()
                if resp.status_code == 200 and res_data.get('success'):
                    return True, res_data.get('message', f'Đã đóng thành công lệnh #{ticket_clean} trên Exness MT5'), res_data
                elif res_data.get('error'):
                    return False, res_data.get('error'), {}
            except Exception as e:
                return False, f"Lỗi kết nối tới MT5 Bridge: {e}", {}

        return False, "Chưa kết nối sàn Exness MT5", {}

    def modify_order(self, ticket: str | int, sl: float, tp: float, symbol: str = "") -> tuple[bool, str]:
        """Cập nhật dời SL / TP cho vị thế mở trên sàn Exness MT5 để khóa lợi nhuận."""
        if not self.is_connected:
            self.connect()

        ticket_clean = int(str(ticket).replace('#', '').strip()) if str(ticket).replace('#', '').strip().isdigit() else 0

        if MT5_AVAILABLE and not self.bridge_mode and self.is_connected and ticket_clean > 0:
            real_symbol = symbol
            all_pos = mt5.positions_get()
            if all_pos:
                for p in all_pos:
                    if int(p.ticket) == ticket_clean:
                        real_symbol = p.symbol
                        break

            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket_clean,
                "symbol": real_symbol,
                "sl": float(sl) if sl > 0 else 0.0,
                "tp": float(tp) if tp > 0 else 0.0,
            }
            res = mt5.order_send(request)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                return True, f"Đã dời SL ({sl}) và TP ({tp}) thành công cho lệnh #{ticket_clean}"
            err_msg = res.comment if res else str(mt5.last_error())
            return False, f"Lỗi MT5 dời SL/TP: {err_msg}"

        if self.bridge_mode or not MT5_AVAILABLE:
            if not self.is_bridge_reachable():
                return False, "MT5 Bridge không khả dụng"
            try:
                resp = requests.post(f"{BRIDGE_URL}/order_modify", json={
                    'ticket': ticket_clean,
                    'sl': float(sl),
                    'tp': float(tp),
                    'symbol': symbol
                }, timeout=1.5)
                if resp.status_code == 200 and resp.json().get('success'):
                    return True, resp.json().get('message', 'Đã cập nhật SL/TP thành công')
            except Exception:
                pass

        return False, "Không thể kết nối MT5 để dời SL/TP"

    def sync_account_info(self, wallet) -> bool:
        """Đồng bộ số dư, vốn (Equity), Lời/Lỗ, Ký quỹ (Margin), Ký quỹ khả dụng (Free Margin) trực tiếp từ Exness MT5."""
        if MT5_AVAILABLE and not self.bridge_mode and self.is_connected:
            account_info = mt5.account_info()
            if account_info:
                live_bal = Decimal(str(round(account_info.balance, 2)))
                live_eq = Decimal(str(round(account_info.equity, 2)))
                live_pnl = Decimal(str(round(account_info.profit, 2)))
                live_margin = Decimal(str(round(account_info.margin, 2)))
                live_margin_free = Decimal(str(round(account_info.margin_free, 2)))
                live_margin_level = float(account_info.margin_level) if account_info.margin_level else 0.0

                wallet.balance = live_bal
                wallet.equity = live_eq
                wallet.floating_pnl = live_pnl
                wallet.margin = live_margin
                wallet.margin_free = live_margin_free
                wallet.margin_level = live_margin_level

                wallet.today_pnl = wallet.get_today_pnl()
                wallet.save(update_fields=['balance_db', 'equity_db', 'floating_pnl', 'margin', 'margin_free', 'margin_level', 'today_pnl'])
                return True

        if self.bridge_mode or not MT5_AVAILABLE:
            if not self.is_bridge_reachable():
                return False
            try:
                payload = {
                    'login': wallet.mt5_login,
                    'password': wallet.mt5_password,
                    'server': wallet.mt5_server
                }
                resp = requests.post(f"{BRIDGE_URL}/account_info", json=payload, timeout=4)
                if resp.status_code == 200 and resp.json().get('success'):
                    acc = resp.json().get('account_info', {})
                    live_bal = Decimal(str(round(acc.get('balance', 0), 2)))
                    live_eq = Decimal(str(round(acc.get('equity', 0), 2)))
                    live_pnl = Decimal(str(round(acc.get('profit', 0), 2)))
                    live_margin = Decimal(str(round(acc.get('margin', 0), 2)))
                    live_margin_free = Decimal(str(round(acc.get('margin_free', 0), 2)))
                    live_margin_level = float(acc.get('margin_level', 0.0))

                    wallet.balance = live_bal
                    wallet.equity = live_eq
                    wallet.floating_pnl = live_pnl
                    wallet.margin = live_margin
                    wallet.margin_free = live_margin_free
                    wallet.margin_level = live_margin_level

                    wallet.today_pnl = wallet.get_today_pnl()
                    wallet.save(update_fields=['balance_db', 'equity_db', 'floating_pnl', 'margin', 'margin_free', 'margin_level', 'today_pnl'])
                    return True
            except Exception:
                pass

        return False

    def sync_positions(self, wallet):
        """Đồng bộ toàn bộ các lệnh đang mở realtime từ Exness MT5 vào Database. 100% dữ liệu gốc, không tự tính."""
        from apps.trading.models import Position

        if MT5_AVAILABLE and not self.bridge_mode and self.is_connected:
            live_positions = mt5.positions_get()
            if live_positions is not None:
                live_tickets = set()
                for pos in live_positions:
                    ticket = str(pos.ticket)
                    live_tickets.add(ticket)
                    pos_type = 'BUY' if pos.type == mt5.POSITION_TYPE_BUY else 'SELL'
                    opened_dt = datetime.fromtimestamp(pos.time, tz=dt_timezone.utc) if pos.time else timezone.now()
                    magic = int(getattr(pos, 'magic', 0) or 0)
                    comment = str(getattr(pos, 'comment', '') or '')
                    is_bot = (magic == 8882026) or comment.upper().startswith('BOT') or comment.upper().startswith('AI-') or ('bot_auto' in comment.lower()) or ('ai_scalp' in comment.lower())
                    source = 'BOT' if is_bot else 'USER'
                    Position.objects.update_or_create(
                        ticket=ticket,
                        defaults={
                            'wallet': wallet,
                            'symbol': pos.symbol.rstrip('m_i'),
                            'position_type': pos_type,
                            'lot_size': float(pos.volume),
                            'open_price': Decimal(str(pos.price_open)),
                            'current_price': Decimal(str(pos.price_current)),
                            'stop_loss': Decimal(str(pos.sl)) if pos.sl > 0 else None,
                            'take_profit': Decimal(str(pos.tp)) if pos.tp > 0 else None,
                            'floating_pnl': Decimal(str(round(pos.profit, 2))),
                            'commission': Decimal(str(round(getattr(pos, 'commission', 0.0) or 0.0, 2))),
                            'swap': Decimal(str(round(getattr(pos, 'swap', 0.0) or 0.0, 2))),
                            'source': source,
                            'magic': magic,
                            'comment': comment,
                            'opened_at': opened_dt,
                        }
                    )
                Position.objects.filter(wallet=wallet).exclude(ticket__in=live_tickets).delete()
                return

        if self.bridge_mode or not MT5_AVAILABLE:
            try:
                payload = {
                    'login': wallet.mt5_login,
                    'password': wallet.mt5_password,
                    'server': wallet.mt5_server
                }
                resp = requests.post(f"{BRIDGE_URL}/positions", json=payload, timeout=4)
                if resp.status_code == 200 and resp.json().get('success'):
                    live_positions = resp.json().get('positions', [])
                    live_tickets = set()
                    for pos in live_positions:
                        ticket = str(pos.get('ticket'))
                        live_tickets.add(ticket)
                        pos_time = pos.get('time')
                        opened_dt = datetime.fromtimestamp(pos_time, tz=dt_timezone.utc) if pos_time else timezone.now()
                        magic = int(pos.get('magic', 0) or 0)
                        comment = str(pos.get('comment', '') or '')
                        is_bot = (magic == 8882026) or comment.upper().startswith('BOT') or comment.upper().startswith('AI-') or ('bot_auto' in comment.lower()) or ('ai_scalp' in comment.lower())
                        source = 'BOT' if is_bot else 'USER'

                        for _ in range(3):
                            try:
                                Position.objects.update_or_create(
                                    ticket=ticket,
                                    defaults={
                                        'wallet': wallet,
                                        'symbol': pos.get('symbol', '').rstrip('m_i'),
                                        'position_type': pos.get('position_type', 'BUY'),
                                        'lot_size': float(pos.get('lot_size', 0.01)),
                                        'open_price': Decimal(str(pos.get('open_price', 0))),
                                        'current_price': Decimal(str(pos.get('current_price', 0))),
                                        'stop_loss': Decimal(str(pos.get('stop_loss'))) if pos.get('stop_loss') else None,
                                        'take_profit': Decimal(str(pos.get('take_profit'))) if pos.get('take_profit') else None,
                                        'floating_pnl': Decimal(str(round(pos.get('profit', 0), 2))),
                                        'commission': Decimal(str(round(pos.get('commission', 0), 2))),
                                        'swap': Decimal(str(round(pos.get('swap', 0), 2))),
                                        'source': source,
                                        'magic': magic,
                                        'comment': comment,
                                        'opened_at': opened_dt,
                                    }
                                )
                                break
                            except Exception:
                                import time
                                time.sleep(0.05)
                    try:
                        Position.objects.filter(wallet=wallet).exclude(ticket__in=live_tickets).delete()
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"Lỗi đồng bộ vị thế MT5: {e}")

    def sync_history_from_mt5(self, wallet):
        """Đồng bộ lịch sử giao dịch đã đóng 100% trực tiếp từ sàn Exness MT5 vào Database, gán tag BOT hoặc USER và tính Net PnL."""
        from apps.trading.models import TradeHistory, Position
        from apps.plans.models import TradingPlan

        deals_data = []
        if MT5_AVAILABLE and not self.bridge_mode and self.is_connected:
            now = datetime.now()
            date_from = now - timedelta(days=90)
            deals = mt5.history_deals_get(date_from, now)
            if deals:
                deals_by_pos = {}
                for d in deals:
                    if getattr(d, 'type', 0) == 2:
                        continue
                    sym_raw = str(getattr(d, 'symbol', '') or '').strip()
                    if not sym_raw:
                        continue

                    pos_id = str(d.position_id or d.ticket)
                    if pos_id not in deals_by_pos:
                        deals_by_pos[pos_id] = {'in': None, 'out': None, 'all': []}
                    deals_by_pos[pos_id]['all'].append(d)
                    if d.entry == 0:
                        deals_by_pos[pos_id]['in'] = d
                    elif d.entry == 1 or d.profit != 0:
                        deals_by_pos[pos_id]['out'] = d

                for pos_id, grp in deals_by_pos.items():
                    out_deal = grp['out']
                    in_deal = grp['in']
                    # Chỉ lấy các lệnh ĐÃ ĐÓNG THỰC SỰ (bắt buộc phải có out_deal)
                    if not out_deal:
                        continue
                    target_deal = out_deal or in_deal
                    sym = str(getattr(target_deal, 'symbol', '') or '').strip()
                    if not sym:
                        continue

                    if in_deal:
                        deal_type = 'BUY' if in_deal.type == 0 else 'SELL'
                        open_p = float(in_deal.price)
                        open_t = datetime.fromtimestamp(in_deal.time, tz=dt_timezone.utc) if in_deal.time else timezone.now()
                    elif out_deal:
                        deal_type = 'BUY' if out_deal.type == 1 else 'SELL'
                        open_p = float(out_deal.price)
                        open_t = datetime.fromtimestamp(out_deal.time, tz=dt_timezone.utc) if out_deal.time else timezone.now()
                    else:
                        deal_type = 'BUY' if target_deal.type == 0 else 'SELL'
                        open_p = float(target_deal.price)
                        open_t = datetime.fromtimestamp(target_deal.time, tz=dt_timezone.utc) if target_deal.time else timezone.now()

                    close_p = float(out_deal.price) if out_deal else open_p
                    close_t = datetime.fromtimestamp(out_deal.time, tz=dt_timezone.utc) if (out_deal and out_deal.time) else open_t

                    all_prices = [float(getattr(d, 'price', 0.0) or 0.0) for d in grp['all'] if float(getattr(d, 'price', 0.0) or 0.0) > 0]
                    if open_p == 0.0 and all_prices:
                        open_p = all_prices[0]
                    if close_p == 0.0 and all_prices:
                        close_p = all_prices[-1]

                    total_profit = float(sum(getattr(d, 'profit', 0.0) or 0.0 for d in grp['all']))
                    total_comm = float(sum(getattr(d, 'commission', 0.0) or 0.0 for d in grp['all']))
                    total_swap = float(sum(getattr(d, 'swap', 0.0) or 0.0 for d in grp['all']))
                    net_pnl_val = round(total_profit + total_comm + total_swap, 2)

                    comment = str(target_deal.comment or '').strip()
                    magic = int(target_deal.magic or 0)
                    # Lấy tập hợp ticket mà bot đã tạo ra trong DB
                    bot_pos_tickets = set(Position.objects.filter(source='BOT').values_list('ticket', flat=True))
                    bot_plan_tickets = set(Position.objects.filter(plan__isnull=False).values_list('ticket', flat=True))
                    bot_hist_tickets = set(TradeHistory.objects.filter(source='BOT').values_list('ticket', flat=True))
                    all_bot_tickets = bot_pos_tickets | bot_plan_tickets | bot_hist_tickets

                    is_bot = (
                        magic == 8882026 or 
                        pos_id in all_bot_tickets or 
                        comment.upper().startswith('BOT') or 
                        comment.upper().startswith('AI') or 
                        any(k in comment.lower() for k in ['ai', 'bot', 'autobot', 'scalp', 'bot_auto', 'ai_scalp'])
                    )
                    source = 'BOT' if is_bot else 'USER'

                    deals_data.append({
                        'ticket': pos_id,
                        'symbol': sym.rstrip('m_i'),
                        'position_type': deal_type,
                        'lot_size': float(target_deal.volume),
                        'open_price': Decimal(str(open_p)),
                        'close_price': Decimal(str(close_p)),
                        'pnl': Decimal(str(net_pnl_val)),
                        'commission': Decimal(str(round(total_comm, 2))),
                        'swap': Decimal(str(round(total_swap, 2))),
                        'pips': 0.0,
                        'close_reason': 'TP_HIT' if is_bot else 'MANUAL_CLOSE',
                        'is_win': net_pnl_val > 0,
                        'source': source,
                        'magic': magic,
                        'comment': comment,
                        'opened_at': open_t,
                        'closed_at': close_t,
                    })

        elif self.bridge_mode or not MT5_AVAILABLE:
            try:
                payload = {
                    'login': wallet.mt5_login,
                    'password': wallet.mt5_password,
                    'server': wallet.mt5_server
                }
                resp = requests.post(f"{BRIDGE_URL}/history_deals", json=payload, timeout=8)
                if resp.status_code == 200 and resp.json().get('success'):
                    deals = resp.json().get('deals', [])
                    bot_pos_tickets = set(Position.objects.filter(source='BOT').values_list('ticket', flat=True))
                    bot_plan_tickets = set(Position.objects.filter(plan__isnull=False).values_list('ticket', flat=True))
                    bot_hist_tickets = set(TradeHistory.objects.filter(source='BOT').values_list('ticket', flat=True))
                    all_bot_tickets = bot_pos_tickets | bot_plan_tickets | bot_hist_tickets
                    active_open_tickets = set(Position.objects.filter(wallet=wallet).values_list('ticket', flat=True))

                    for d in deals:
                        ticket_str = str(d.get('ticket', '')).strip()
                        # Bỏ qua nếu lệnh này đang là vị thế mở và chưa có profit thực tế
                        if ticket_str in active_open_tickets and float(d.get('profit', 0.0)) == 0.0:
                            continue
                        sym = str(d.get('symbol', '')).strip()
                        if not sym:
                            continue
                        open_time_val = d.get('open_time') or d.get('time', 0)
                        close_time_val = d.get('time', 0)
                        opened_dt = datetime.fromtimestamp(open_time_val, tz=dt_timezone.utc) if open_time_val else timezone.now()
                        closed_dt = datetime.fromtimestamp(close_time_val, tz=dt_timezone.utc) if close_time_val else timezone.now()
                        
                        deal_magic = int(d.get('magic', 0) or 0)
                        deal_comment = str(d.get('comment', '') or '').strip()

                        # Đối chiếu với DB dựa vào ticket id và magic/comment để xác định nguồn BOT hay USER
                        is_bot = (
                            ticket_str in all_bot_tickets or
                            deal_magic == 8882026 or
                            d.get('source') == 'BOT' or
                            deal_comment.upper().startswith('BOT') or
                            deal_comment.upper().startswith('AI') or
                            any(k in deal_comment.lower() for k in ['ai', 'bot', 'autobot', 'scalp', 'bot_auto', 'ai_scalp'])
                        )
                        source = 'BOT' if is_bot else 'USER'

                        comm_d = float(d.get('commission', 0.0) or 0.0)
                        swap_d = float(d.get('swap', 0.0) or 0.0)
                        net_pnl_val = round(float(d.get('profit', 0.0)) + comm_d + swap_d, 2)

                        open_p_val = float(d.get('open_price') or d.get('price') or 0.0)
                        close_p_val = float(d.get('close_price') or d.get('price') or open_p_val)
                        if open_p_val == 0.0 and close_p_val > 0.0:
                            open_p_val = close_p_val
                        if close_p_val == 0.0 and open_p_val > 0.0:
                            close_p_val = open_p_val

                        deals_data.append({
                            'ticket': ticket_str,
                            'symbol': sym.rstrip('m_i'),
                            'position_type': d.get('position_type', 'BUY'),
                            'lot_size': float(d.get('lot_size', 0.01)),
                            'open_price': Decimal(str(open_p_val)),
                            'close_price': Decimal(str(close_p_val)),
                            'pnl': Decimal(str(net_pnl_val)),
                            'commission': Decimal(str(round(comm_d, 2))),
                            'swap': Decimal(str(round(swap_d, 2))),
                            'pips': 0.0,
                            'close_reason': 'MANUAL_CLOSE' if source == 'USER' else 'TP_HIT',
                            'is_win': net_pnl_val > 0,
                            'source': source,
                            'magic': deal_magic,
                            'comment': deal_comment,
                            'opened_at': opened_dt,
                            'closed_at': closed_dt,
                        })
            except Exception as e:
                logger.error(f"Lỗi đồng bộ lịch sử MT5: {e}")

        # Dọn sạch các bản ghi TradeHistory bị trùng với các vị thế đang mở
        try:
            active_all_tickets = set(Position.objects.filter(wallet=wallet).values_list('ticket', flat=True))
            if active_all_tickets:
                TradeHistory.objects.filter(wallet=wallet, ticket__in=active_all_tickets, pnl=0).delete()
        except Exception:
            pass

        # Ghi vào DB chuẩn xác bằng bulk_create update_conflicts để chạy trong <10ms và không khóa DB
        if deals_data:
            from django.db import transaction
            hist_objs = []
            for item in deals_data:
                item_copy = dict(item)
                t = str(item_copy.pop('ticket', ''))
                hist_objs.append(TradeHistory(
                    wallet=wallet,
                    ticket=t,
                    **item_copy
                ))

            update_cols = ['symbol', 'position_type', 'lot_size', 'open_price', 'close_price', 'pnl', 'commission', 'swap', 'pips', 'close_reason', 'is_win', 'source', 'magic', 'comment', 'opened_at', 'closed_at']

            for attempt in range(5):
                try:
                    with transaction.atomic():
                        TradeHistory.objects.bulk_create(
                            hist_objs,
                            update_conflicts=True,
                            unique_fields=['wallet', 'ticket'],
                            update_fields=update_cols
                        )
                        # Xóa bỏ hoàn toàn các bản ghi rác không tồn tại trên sàn Exness MT5
                        live_deal_tickets = set(h.ticket for h in hist_objs)
                        TradeHistory.objects.filter(wallet=wallet).exclude(ticket__in=live_deal_tickets).delete()
                        if wallet:
                            wallet.calculate_metrics()
                            wallet.save()
                    break
                except Exception as db_err:
                    time.sleep(0.05)

