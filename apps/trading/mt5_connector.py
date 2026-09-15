import os
import time
import logging
import requests
from datetime import datetime, timedelta, timezone as dt_timezone
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

    @classmethod
    def is_wine_bridge_open(cls) -> bool:
        """Chỉ True khi cổng Wine Bridge 9999 thực sự mở — không dựa vào import MetaTrader5."""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.04)
            res = s.connect_ex(('127.0.0.1', 9999))
            s.close()
            return res == 0
        except Exception:
            return False

    @staticmethod
    def normalize_symbol(raw: str) -> str:
        """Chuẩn hóa mã Exness (XAUUSDm / BTCUSDm) về mã gốc XAUUSD / BTCUSD."""
        s = str(raw or '').strip()
        if not s:
            return ''
        upper = s.upper()
        for suffix in ('.PRO', '.S', '_I'):
            if upper.endswith(suffix):
                upper = upper[:-len(suffix)]
                break
        if len(upper) > 4 and upper.endswith('M'):
            upper = upper[:-1]
        return upper

    @classmethod
    def symbol_candidates(cls, symbol: str) -> list:
        """Các biến thể mã trên Exness MT5 (suffix m, _i, c)."""
        raw = str(symbol or '').strip()
        base = cls.normalize_symbol(raw) or raw
        out = []
        for cand in (raw, base, f'{base}m', f'{base}M', f'{base}_i', f'{base}c', raw.upper(), raw.lower()):
            if cand and cand not in out:
                out.append(cand)
        return out

    @classmethod
    def ensure_native_initialized(cls) -> bool:
        from apps.trading.mt5_session import MT5NativeSession
        return MT5NativeSession.ensure()

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
                from apps.trading.mt5_session import MT5NativeSession
                acc_now = MT5NativeSession.account()
                if acc_now and int(acc_now.login) != int(login_str):
                    return False, MT5NativeSession.explain_single_account(
                        acc_now.login, getattr(acc_now, 'server', ''), login_str
                    ), {
                        'live_login': int(acc_now.login),
                        'requested_login': int(login_str),
                    }
                if MT5NativeSession.login(int(login_str), password, server_clean):
                    acc = MT5NativeSession.account()
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
                err = None
                try:
                    import MetaTrader5 as _mt5
                    err = _mt5.last_error()
                except Exception:
                    pass
                if err:
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

    @classmethod
    def activate_wallet_session(cls, login, password, server, account_type='DEMO') -> tuple[bool, str, dict]:
        """Khi kích hoạt ví: mở MT5 → login tài khoản (được phép đổi) → trả account_info."""
        from apps.trading.mt5_launcher import MT5Launcher

        if not login or not str(login).strip():
            return False, 'Số tài khoản MT5 không được để trống.', {}
        if not password or not str(password).strip():
            return False, 'Mật khẩu MT5 không được để trống.', {}
        if not server or not str(server).strip():
            return False, 'Máy chủ Exness không được để trống.', {}

        login_str = str(login).replace('#', '').strip()
        if not login_str.isdigit():
            return False, f"Số tài khoản MT5 '{login_str}' không hợp lệ.", {}
        server_clean = str(server).strip()

        try:
            MT5Launcher.ensure_terminal_running()
        except Exception:
            pass

        if not MT5_AVAILABLE:
            return cls.test_connection(login_str, password, server_clean, account_type=account_type)

        try:
            from apps.trading.mt5_session import MT5NativeSession
            if not MT5NativeSession.ensure():
                return False, 'Không khởi tạo được kết nối IPC tới MetaTrader 5.', {}

            ok = MT5NativeSession.login(
                int(login_str), password, server_clean, allow_switch=True
            )
            if not ok:
                err = None
                try:
                    import MetaTrader5 as _mt5
                    err = _mt5.last_error()
                except Exception:
                    pass
                detail = f' Mã lỗi MT5: {err}' if err else ''
                return False, f'Đăng nhập MT5 #{login_str} thất bại.{detail}', {}

            acc = MT5NativeSession.account()
            if not acc:
                return False, 'Đã gửi login nhưng MT5 không trả account_info.', {}

            snap = MT5NativeSession.terminal_snapshot()
            algo_on = bool(snap.get('trade_allowed'))
            info = {
                'login': acc.login,
                'server': acc.server,
                'balance': float(acc.balance),
                'equity': float(acc.equity),
                'leverage': acc.leverage,
                'currency': acc.currency,
                'mode': 'EXNESS_LIVE_MT5',
                'algo_trading': algo_on,
            }
            msg = (
                f'Đã login MT5 #{acc.login} ({acc.server}) — Số dư: ${acc.balance:,.2f}'
            )
            return True, msg, info
        except Exception as e:
            logger.warning('activate_wallet_session error: %s', e)
            return False, f'Lỗi kích hoạt ví trên MT5: {e}', {}

    def connect(self, allow_switch: bool = False) -> bool:
        """Khởi tạo kết nối tới MT5 Terminal (Native hoặc Wine Bridge)."""
        if not self.login:
            return False

        login_clean = int(str(self.login).replace('#', '').strip()) if str(self.login).replace('#', '').strip().isdigit() else 0
        if not login_clean:
            return False

        # 1. Native Windows
        if MT5_AVAILABLE:
            try:
                from apps.trading.mt5_session import MT5NativeSession
                if MT5NativeSession.login(login_clean, self.password or '', self.server or '', allow_switch=allow_switch):
                    self.is_connected = True
                    return True
                acc = MT5NativeSession.account()
                if acc:
                    logger.warning(
                        "MT5 đang login #%s, yêu cầu #%s. Cần đăng nhập đúng tài khoản trên terminal.",
                        acc.login, login_clean
                    )
            except Exception as e:
                logger.warning("MT5 native connect error: %s", e)

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
        """Đánh dấu ngắt kết nối logic. Không shutdown() IPC — process Django/bot vẫn cần terminal sống."""
        self.is_connected = False
        self.bridge_mode = False

    def get_symbol_price(self, symbol: str) -> dict:
        """Lấy giá Bid/Ask/Spread mới nhất của cặp giao dịch."""
        if MT5_AVAILABLE and self.is_connected and not self.bridge_mode:
            from apps.trading.mt5_session import MT5NativeSession
            ticks = MT5NativeSession.snapshot_ticks([symbol])
            payload = ticks.get(self.normalize_symbol(symbol)) or ticks.get(symbol)
            if payload:
                return {
                    'bid': payload['bid'],
                    'ask': payload['ask'],
                    'last': payload['last'],
                    'spread_pips': payload['spread_pips'],
                    'time': payload['time'],
                }

        if self.bridge_mode or not MT5_AVAILABLE:
            try:
                resp = requests.post(f"{BRIDGE_URL}/symbol_price", json={'symbol': symbol}, timeout=4)
                if resp.status_code == 200 and resp.json().get('success'):
                    return resp.json().get('price')
            except Exception:
                pass

        return None

    def send_order(self, symbol: str, order_type: str, volume: float, price: float = 0.0, sl: float = 0.0, tp: float = 0.0, comment: str = "AutoBot", magic: int = 8882026) -> dict:
        """Gửi lệnh Mua/Bán lên sàn Exness MT5."""
        if not self.is_connected:
            self.connect()
        if not self.is_connected:
            return {'success': False, 'error': 'Chưa kết nối sàn Exness MT5. Hãy mở MT5, đăng nhập và bật Algo Trading.'}

        if MT5_AVAILABLE and not self.bridge_mode and self.is_connected:
            from apps.trading.mt5_session import MT5NativeSession
            return MT5NativeSession.send_market(
                symbol=symbol,
                order_type=order_type,
                volume=volume,
                sl=sl,
                tp=tp,
                comment=comment,
                magic=magic,
            )

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

    def close_order(self, ticket: int, symbol: str, order_type: str, volume: float, comment: str = 'Close') -> tuple[bool, str, dict]:
        """
        Đóng lệnh trên Exness MT5.
        Trả về (success: bool, message: str, data: dict).
        """
        if not self.is_connected:
            self.connect()

        ticket_clean = int(str(ticket).replace('#', '').strip()) if str(ticket).replace('#', '').strip().isdigit() else 0

        if not self.is_connected:
            return False, "Chưa kết nối sàn Exness MT5. Hãy mở MT5, đăng nhập và bật Algo Trading.", {}

        if MT5_AVAILABLE and not self.bridge_mode and self.is_connected:
            from apps.trading.mt5_session import MT5NativeSession
            return MT5NativeSession.close_market(
                ticket=ticket_clean,
                symbol=symbol,
                order_type=order_type,
                volume=volume,
                comment=comment or 'Close',
            )

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
            from apps.trading.mt5_session import MT5NativeSession
            return MT5NativeSession.modify_sltp(ticket_clean, sl, tp, symbol)

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
            from apps.trading.mt5_session import MT5NativeSession
            account_info = MT5NativeSession.account()
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
            from apps.trading.mt5_session import MT5NativeSession
            acc = MT5NativeSession.account()
            if acc is None:
                logger.warning("sync_positions: account_info=None, bỏ qua để không xóa lệnh trong DB")
                return False
            live_positions = MT5NativeSession.positions()
            if live_positions is None:
                logger.warning("sync_positions: positions_get=None, bỏ qua để không xóa lệnh trong DB")
                return False
            live_tickets = set()
            for pos in live_positions:
                ticket = str(pos.ticket)
                live_tickets.add(ticket)
                pos_type = 'BUY' if pos.type == mt5.POSITION_TYPE_BUY else 'SELL'
                opened_dt = datetime.fromtimestamp(pos.time, tz=dt_timezone.utc) if pos.time else timezone.now()
                magic = int(getattr(pos, 'magic', 0) or 0)
                comment = str(getattr(pos, 'comment', '') or '')
                from apps.trading.order_source import classify_order_source, remember_order_source
                source = classify_order_source(magic, comment, ticket)
                remember_order_source(ticket, source, magic)
                defaults = {
                    'wallet': wallet,
                    'symbol': self.normalize_symbol(pos.symbol) or pos.symbol,
                    'position_type': pos_type,
                    'lot_size': float(pos.volume),
                    'open_price': Decimal(str(pos.price_open)),
                    'current_price': Decimal(str(pos.price_current)),
                    'floating_pnl': Decimal(str(round(pos.profit, 2))),
                    'commission': Decimal(str(round(getattr(pos, 'commission', 0.0) or 0.0, 2))),
                    'swap': Decimal(str(round(getattr(pos, 'swap', 0.0) or 0.0, 2))),
                    'source': source,
                    'magic': magic,
                    'comment': comment,
                    'opened_at': opened_dt,
                }
                if pos.sl > 0:
                    defaults['stop_loss'] = Decimal(str(pos.sl))
                if pos.tp > 0:
                    defaults['take_profit'] = Decimal(str(pos.tp))
                Position.objects.update_or_create(
                    ticket=ticket,
                    defaults=defaults,
                )
            extras = Position.objects.filter(wallet=wallet).exclude(ticket__in=live_tickets)
            extras = extras.exclude(ticket__startswith='LOCAL-')
            extras.delete()
            return True

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
                        from apps.trading.order_source import classify_order_source, remember_order_source
                        source = classify_order_source(magic, comment, ticket)
                        remember_order_source(ticket, source, magic)

                        for _ in range(3):
                            try:
                                Position.objects.update_or_create(
                                    ticket=ticket,
                                    defaults={
                                        'wallet': wallet,
                                        'symbol': self.normalize_symbol(pos.get('symbol', '')) or pos.get('symbol', ''),
                                        'position_type': pos.get('position_type', 'BUY'),
                                        'lot_size': float(pos.get('lot_size', 0.01)),
                                        'open_price': Decimal(str(pos.get('open_price', 0))),
                                        'current_price': Decimal(str(pos.get('current_price', 0))),
                                        **({'stop_loss': Decimal(str(pos.get('stop_loss')))} if pos.get('stop_loss') else {}),
                                        **({'take_profit': Decimal(str(pos.get('take_profit')))} if pos.get('take_profit') else {}),
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
                        extras = Position.objects.filter(wallet=wallet).exclude(ticket__in=live_tickets)
                        extras.exclude(ticket__startswith='LOCAL-').delete()
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"Lỗi đồng bộ vị thế MT5: {e}")

    def sync_history_from_mt5(self, wallet):
        """Đồng bộ lịch sử giao dịch đã đóng 100% trực tiếp từ sàn Exness MT5 vào Database, gán tag BOT hoặc USER và tính Net PnL."""
        from apps.trading.models import TradeHistory, Position

        deals_data = []
        if not self.is_connected:
            self.connect()

        if MT5_AVAILABLE and not self.bridge_mode and self.is_connected:
            from apps.trading.mt5_session import MT5NativeSession
            now = datetime.now()
            date_from = MT5NativeSession.history_from_for_wallet(getattr(wallet, 'id', 0) or 0)
            deals = MT5NativeSession.history_deals(date_from, now)
            if deals is None:
                logger.warning("history_deals_get trả None — giữ lịch sử DB hiện tại")
            else:
                MT5NativeSession.mark_history_synced(getattr(wallet, 'id', 0) or 0)
                from apps.trading.order_source import remember_order_source
                for row in MT5NativeSession.group_closed_deals(deals):
                    remember_order_source(row['ticket'], row['source'], row.get('magic') or 0)
                    deals_data.append({
                        'ticket': row['ticket'],
                        'symbol': row['symbol'],
                        'position_type': row['position_type'],
                        'lot_size': row['lot_size'],
                        'open_price': Decimal(str(row['open_price'])),
                        'close_price': Decimal(str(row['close_price'])),
                        'pnl': Decimal(str(row['pnl'])),
                        'commission': Decimal(str(row['commission'])),
                        'swap': Decimal(str(row['swap'])),
                        'pips': row.get('pips') or 0.0,
                        'close_reason': row['close_reason'],
                        'is_win': row['is_win'],
                        'source': row['source'],
                        'magic': row['magic'],
                        'comment': row['comment'],
                        'opened_at': row['opened_at'],
                        'closed_at': row['closed_at'],
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

                        from apps.trading.order_source import resolve_close_reason
                        close_reason = resolve_close_reason(
                            ticket=ticket_str,
                            deal_reason=d.get('reason'),
                            comment=deal_comment,
                            source=source,
                        )

                        deals_data.append({
                            'ticket': ticket_str,
                            'symbol': self.normalize_symbol(sym) or sym,
                            'position_type': d.get('position_type', 'BUY'),
                            'lot_size': float(d.get('lot_size', 0.01)),
                            'open_price': Decimal(str(open_p_val)),
                            'close_price': Decimal(str(close_p_val)),
                            'pnl': Decimal(str(net_pnl_val)),
                            'commission': Decimal(str(round(comm_d, 2))),
                            'swap': Decimal(str(round(swap_d, 2))),
                            'pips': 0.0,
                            'close_reason': close_reason,
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
                        if wallet:
                            wallet.calculate_metrics()
                            wallet.save()
                    break
                except Exception as db_err:
                    time.sleep(0.05)

