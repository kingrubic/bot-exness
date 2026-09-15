"""
Native MetaTrader 5 IPC session.

Fastest accurate path on Windows: one persistent initialize(), one RLock
(Django request threads + bot worker), cached Exness symbols, broker ticks /
positions / deals — never fake prices when the terminal is live.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

# Trade retcodes (official MQL5 / MetaTrader5 package)
RET_REQUOTE = 10004
RET_PLACED = 10008
RET_DONE = 10009
RET_DONE_PARTIAL = 10010
RET_TIMEOUT = 10012
RET_INVALID_STOPS = 10016
RET_MARKET_CLOSED = 10018
RET_NO_MONEY = 10019
RET_PRICE_CHANGED = 10020
RET_PRICE_OFF = 10021
RET_TOO_MANY = 10024
RET_NO_CHANGES = 10025
RET_AT_DISABLED_SERVER = 10026
RET_AT_DISABLED_CLIENT = 10027
RET_INVALID_FILL = 10030
RET_POSITION_CLOSED = 10036

RETRY_RETCODES = {RET_REQUOTE, RET_TIMEOUT, RET_PRICE_CHANGED, RET_PRICE_OFF, RET_TOO_MANY}
SUCCESS_RETCODES = {RET_DONE, RET_DONE_PARTIAL, RET_PLACED}

TIMEFRAME_MAP = {
    'M1': 1,
    'M5': 5,
    'M15': 15,
    'M30': 30,
    'H1': 16385,
    'H4': 16388,
    'D1': 16408,
}

_LOCK = threading.RLock()


class MT5NativeSession:
    """Process-wide native MT5 IPC. All mt5.* calls go through `locked()`."""

    _ready = False
    _last_init_try = 0.0
    _login = 0
    _symbol_map: dict[str, str] = {}
    _spec_cache: dict[str, dict] = {}
    _tick_cache: dict[str, dict] = {}
    _history_cursor: dict[int, datetime] = {}

    @staticmethod
    def normalize_symbol(raw: str) -> str:
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
        raw = str(symbol or '').strip()
        base = cls.normalize_symbol(raw) or raw
        out = []
        for cand in (raw, base, f'{base}m', f'{base}M', f'{base}_i', f'{base}c', raw.upper(), raw.lower()):
            if cand and cand not in out:
                out.append(cand)
        return out

    @classmethod
    def locked(cls, fn, *args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)

    @classmethod
    def available(cls) -> bool:
        return bool(MT5_AVAILABLE)

    @classmethod
    def cached_ticks(cls) -> dict:
        return dict(cls._tick_cache)

    @classmethod
    def ensure(cls) -> bool:
        if not MT5_AVAILABLE:
            return False
        with _LOCK:
            try:
                if mt5.terminal_info() is not None:
                    cls._ready = True
                    return True
            except Exception:
                pass

            now = time.time()
            if cls._ready is False and (now - cls._last_init_try) < 2.0:
                return False
            cls._last_init_try = now

            path = os.environ.get('MT5_TERMINAL_PATH', '').strip()
            if not path:
                try:
                    from apps.trading.mt5_launcher import MT5Launcher
                    path = MT5Launcher.get_mt5_path() or ''
                except Exception:
                    path = ''
            kwargs = {'timeout': 8000}
            if path:
                kwargs['path'] = path
            try:
                ok = bool(mt5.initialize(**kwargs))
                cls._ready = ok
                if not ok:
                    logger.warning("MT5 initialize failed: %s", mt5.last_error())
                return ok
            except Exception as e:
                cls._ready = False
                logger.warning("MT5 initialize exception: %s", e)
                return False

    @classmethod
    def terminal_snapshot(cls) -> dict:
        """Login + nút Algo Trading (terminal_info.trade_allowed) từ IPC đang mở."""
        empty = {
            'ready': False,
            'logged_in': False,
            'trade_allowed': False,
            'connected': False,
            'account': None,
            'error': None,
        }
        if not cls.ensure():
            err = None
            try:
                if MT5_AVAILABLE:
                    err = str(mt5.last_error())
            except Exception:
                pass
            empty['error'] = err
            return empty
        with _LOCK:
            term = mt5.terminal_info()
            acc = mt5.account_info()
        account = None
        logged_in = False
        if acc:
            logged_in = True
            account = {
                'login': int(acc.login),
                'server': str(acc.server or ''),
                'balance': float(acc.balance),
            }
        return {
            'ready': True,
            'logged_in': logged_in,
            'trade_allowed': bool(term and getattr(term, 'trade_allowed', False)),
            'connected': bool(term and getattr(term, 'connected', False)),
            'account': account,
            'error': None,
        }

    @classmethod
    def login(cls, login: int, password: str, server: str) -> bool:
        if not cls.ensure():
            return False
        with _LOCK:
            acc = mt5.account_info()
            if acc and int(acc.login) == int(login):
                cls._login = int(login)
                return True
            if not password:
                return bool(acc and int(acc.login) == int(login))
            ok = bool(mt5.login(login=int(login), password=password, server=server or ''))
            if ok:
                cls._login = int(login)
            else:
                logger.warning("MT5 login #%s failed: %s", login, mt5.last_error())
            return ok

    @classmethod
    def account(cls) -> Optional[Any]:
        if not cls.ensure():
            return None
        with _LOCK:
            return mt5.account_info()

    @classmethod
    def resolve_symbol(cls, symbol: str) -> Optional[str]:
        base = cls.normalize_symbol(symbol) or str(symbol or '').strip()
        if not base:
            return None
        cached = cls._symbol_map.get(base) or cls._symbol_map.get(symbol)
        if cached:
            return cached
        if not cls.ensure():
            return None
        with _LOCK:
            cached = cls._symbol_map.get(base)
            if cached:
                return cached
            for cand in cls.symbol_candidates(symbol):
                try:
                    mt5.symbol_select(cand, True)
                    info = mt5.symbol_info(cand)
                except Exception:
                    continue
                if not info:
                    continue
                trade_mode = int(getattr(info, 'trade_mode', 4) or 0)
                if trade_mode == 0:
                    continue
                cls._symbol_map[base] = cand
                cls._symbol_map[str(symbol).strip()] = cand
                cls._spec_cache[cand] = cls._spec_from_info(info)
                return cand
        return None

    @staticmethod
    def _spec_from_info(info) -> dict:
        filling = int(getattr(info, 'filling_mode', 0) or 0)
        modes = []
        if filling & 1:
            modes.append(mt5.ORDER_FILLING_FOK)
        if filling & 2:
            modes.append(mt5.ORDER_FILLING_IOC)
        if filling & 4:
            modes.append(mt5.ORDER_FILLING_RETURN)
        if not modes:
            modes = [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]
        return {
            'filling_modes': modes,
            'volume_min': float(info.volume_min or 0.01),
            'volume_max': float(info.volume_max or 100),
            'volume_step': float(info.volume_step or 0.01),
            'point': float(info.point or 0.01),
            'digits': int(info.digits or 5),
            'trade_stops_level': int(getattr(info, 'trade_stops_level', 0) or 0),
        }

    @classmethod
    def spec(cls, broker_symbol: str) -> dict:
        spec = cls._spec_cache.get(broker_symbol)
        if spec:
            return spec
        if not cls.ensure():
            return {}
        with _LOCK:
            info = mt5.symbol_info(broker_symbol)
            if not info:
                return {}
            spec = cls._spec_from_info(info)
            cls._spec_cache[broker_symbol] = spec
            return spec

    @classmethod
    def snapshot_ticks(cls, symbols: list[str]) -> dict:
        """Bid/Ask/Last from the terminal. Updates in-memory cache. No DB."""
        out = {}
        if not cls.ensure():
            return dict(cls._tick_cache)
        with _LOCK:
            for raw in symbols:
                broker = cls._symbol_map.get(raw) or cls._symbol_map.get(
                    str(raw).upper()
                )
                if not broker:
                    # resolve without nested lock deadlock — already holding _LOCK
                    broker = None
                    base = cls.normalize_symbol(raw) or raw
                    for cand in cls.symbol_candidates(raw):
                        try:
                            mt5.symbol_select(cand, True)
                            info = mt5.symbol_info(cand)
                        except Exception:
                            continue
                        if info and int(getattr(info, 'trade_mode', 4) or 0) != 0:
                            broker = cand
                            cls._symbol_map[base] = cand
                            cls._spec_cache[cand] = cls._spec_from_info(info)
                            break
                if not broker:
                    continue
                tick = mt5.symbol_info_tick(broker)
                if not tick:
                    continue
                last = float(tick.last or tick.bid or tick.ask or 0)
                bid = float(tick.bid or last)
                ask = float(tick.ask or last)
                if last <= 0:
                    continue
                spec = cls._spec_cache.get(broker) or {}
                point = float(spec.get('point') or 0)
                if not point:
                    info = mt5.symbol_info(broker)
                    point = float(info.point) if info and info.point else 0.01
                spread = round((ask - bid) / (point * 10), 2) if bid and ask and point else 0.0
                base = cls.normalize_symbol(raw) or raw
                payload = {
                    'symbol': base,
                    'broker_symbol': broker,
                    'bid': bid,
                    'ask': ask,
                    'last': last,
                    'spread_pips': spread,
                    'time': int(getattr(tick, 'time', 0) or 0),
                    'digits': int(spec.get('digits') or 5),
                }
                out[base] = payload
                cls._tick_cache[base] = payload
        return out

    @classmethod
    def positions(cls) -> Optional[list]:
        if not cls.ensure():
            return None
        with _LOCK:
            rows = mt5.positions_get()
            if rows is None:
                return None
            return list(rows)

    @classmethod
    def positions_by_ticket(cls) -> dict[str, Any]:
        rows = cls.positions()
        if not rows:
            return {}
        return {str(p.ticket): p for p in rows}

    @classmethod
    def pending_orders(cls) -> list:
        if not cls.ensure():
            return []
        with _LOCK:
            rows = mt5.orders_get()
            if rows is None:
                return []
            return list(rows)

    @classmethod
    def position_by_ticket(cls, ticket: int):
        if not cls.ensure() or ticket <= 0:
            return None
        with _LOCK:
            rows = mt5.positions_get(ticket=ticket)
            if rows:
                return rows[0]
            return None

    @classmethod
    def copy_rates(cls, symbol: str, timeframe: str = 'M15', count: int = 120) -> list:
        broker = cls.resolve_symbol(symbol)
        if not broker:
            return []
        tf = TIMEFRAME_MAP.get(str(timeframe).upper(), 15)
        with _LOCK:
            rates = mt5.copy_rates_from_pos(broker, tf, 0, int(count))
        if rates is None:
            return []
        return list(rates)

    @classmethod
    def history_deals(cls, date_from: datetime, date_to: datetime | None = None):
        if not cls.ensure():
            return None
        date_to = date_to or datetime.now()
        with _LOCK:
            return mt5.history_deals_get(date_from, date_to)

    @classmethod
    def history_from_for_wallet(cls, wallet_id: int) -> datetime:
        prev = cls._history_cursor.get(wallet_id)
        if prev:
            return prev - timedelta(hours=2)
        return datetime.now() - timedelta(days=90)

    @classmethod
    def mark_history_synced(cls, wallet_id: int):
        cls._history_cursor[wallet_id] = datetime.now()

    _today_pnl_cache = {'ts': 0.0, 'day': None, 'data': None}

    @classmethod
    def today_realized_pnl(cls) -> dict:
        """Lãi/lỗ đã đóng trong ngày (local midnight) từ history_deals MT5, không lấy TradeHistory DB."""
        empty = {'all': 0.0, 'BOT': 0.0, 'USER': 0.0, 'ok': False}
        if not cls.available() or not cls.ensure():
            return empty
        now_dt = datetime.now()
        day = now_dt.date()
        now = time.time()
        cached = cls._today_pnl_cache
        if cached.get('data') and cached.get('day') == day and (now - cached.get('ts', 0)) < 3.0:
            return cached['data']
        date_from = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        deals = cls.history_deals(date_from, now_dt)
        if deals is None:
            return empty
        bot = 0.0
        user = 0.0
        try:
            from apps.trading.order_source import classify_order_source
        except Exception:
            classify_order_source = None
        for d in deals:
            deal_type = int(getattr(d, 'type', -1) or -1)
            if deal_type not in (0, 1):
                continue
            net = (
                float(getattr(d, 'profit', 0) or 0)
                + float(getattr(d, 'commission', 0) or 0)
                + float(getattr(d, 'swap', 0) or 0)
            )
            magic = int(getattr(d, 'magic', 0) or 0)
            comment = str(getattr(d, 'comment', '') or '')
            pos_id = str(int(getattr(d, 'position_id', 0) or 0) or getattr(d, 'ticket', '') or '')
            src = 'USER'
            if classify_order_source:
                try:
                    src = classify_order_source(magic, comment, pos_id)
                except Exception:
                    src = 'BOT' if magic == 8882026 else 'USER'
            elif magic == 8882026:
                src = 'BOT'
            if src == 'BOT':
                bot += net
            else:
                user += net
        data = {
            'all': round(bot + user, 2),
            'BOT': round(bot, 2),
            'USER': round(user, 2),
            'ok': True,
        }
        cls._today_pnl_cache = {'ts': now, 'day': day, 'data': data}
        return data

    @classmethod
    def _normalize_volume(cls, volume: float, spec: dict) -> float:
        vmin = float(spec.get('volume_min') or 0.01)
        vmax = float(spec.get('volume_max') or 100)
        step = float(spec.get('volume_step') or 0.01)
        v = max(vmin, min(vmax, float(volume)))
        if step > 0:
            v = round(round(v / step) * step, 8)
            if v < vmin:
                v = vmin
        return v

    @classmethod
    def send_market(
        cls,
        symbol: str,
        order_type: str,
        volume: float,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = 'AutoBot',
        magic: int = 8882026,
    ) -> dict:
        broker = cls.resolve_symbol(symbol)
        if not broker:
            return {'success': False, 'error': f'Không tìm thấy cặp {symbol} trên Exness MT5.'}
        spec = cls.spec(broker)
        vol = cls._normalize_volume(volume, spec)
        modes = list(spec.get('filling_modes') or [])
        side = mt5.ORDER_TYPE_BUY if str(order_type).upper() == 'BUY' else mt5.ORDER_TYPE_SELL
        last_err = 'MT5 từ chối lệnh'
        with _LOCK:
            for attempt in range(3):
                tick = mt5.symbol_info_tick(broker)
                price = float(tick.ask if side == mt5.ORDER_TYPE_BUY else tick.bid) if tick else 0.0
                if price <= 0:
                    return {'success': False, 'error': f'Chưa có tick {broker} từ MT5.'}
                for f_mode in modes:
                    req = {
                        'action': mt5.TRADE_ACTION_DEAL,
                        'symbol': broker,
                        'volume': vol,
                        'type': side,
                        'price': price,
                        'sl': float(sl) if sl else 0.0,
                        'tp': float(tp) if tp else 0.0,
                        'deviation': 50,
                        'magic': int(magic or 0),
                        'comment': (comment or '')[:31],
                        'type_time': mt5.ORDER_TIME_GTC,
                        'type_filling': f_mode,
                    }
                    res = mt5.order_send(req)
                    if res is None:
                        last_err = str(mt5.last_error())
                        continue
                    if res.retcode in SUCCESS_RETCODES:
                        if f_mode in modes:
                            spec['filling_modes'] = [f_mode] + [m for m in modes if m != f_mode]
                            cls._spec_cache[broker] = spec
                        return {
                            'success': True,
                            'ticket': str(res.order),
                            'deal': str(res.deal),
                            'price': float(res.price),
                            'volume': float(res.volume),
                            'comment': res.comment or comment,
                            'broker_symbol': broker,
                        }
                    if res.retcode == RET_INVALID_FILL:
                        last_err = res.comment or 'Invalid filling'
                        continue
                    if res.retcode == RET_INVALID_STOPS:
                        req['sl'] = 0.0
                        req['tp'] = 0.0
                        res2 = mt5.order_send(req)
                        if res2 and res2.retcode in SUCCESS_RETCODES:
                            return {
                                'success': True,
                                'ticket': str(res2.order),
                                'deal': str(res2.deal),
                                'price': float(res2.price),
                                'volume': float(res2.volume),
                                'comment': res2.comment or comment,
                                'broker_symbol': broker,
                            }
                        last_err = (res2.comment if res2 else res.comment) or 'Invalid stops'
                        break
                    if res.retcode in RETRY_RETCODES:
                        last_err = res.comment or str(res.retcode)
                        time.sleep(0.05)
                        break
                    if res.retcode in (RET_AT_DISABLED_CLIENT, RET_AT_DISABLED_SERVER):
                        return {'success': False, 'error': 'Algo Trading đang tắt trên MT5. Hãy bật AutoTrading.'}
                    if res.retcode == RET_MARKET_CLOSED:
                        return {'success': False, 'error': f'Thị trường {broker} đang đóng.'}
                    if res.retcode == RET_NO_MONEY:
                        return {'success': False, 'error': 'Không đủ ký quỹ / số dư để mở lệnh.'}
                    last_err = f"{res.comment} (retcode {res.retcode})"
                    break
                else:
                    continue
        return {'success': False, 'error': f'Lỗi MT5 mở lệnh: {last_err}'}

    @classmethod
    def close_market(cls, ticket: int, symbol: str = '', order_type: str = 'BUY', volume: float = 0.01) -> tuple[bool, str, dict]:
        if ticket <= 0:
            return False, 'Ticket không hợp lệ', {}
        pos = cls.position_by_ticket(ticket)
        if pos is None:
            if not cls.ensure():
                return False, 'Chưa kết nối sàn Exness MT5. Hãy mở MT5, đăng nhập và bật Algo Trading.', {}
            rows = cls.positions()
            if rows is None:
                return False, 'Không lấy được danh sách vị thế từ MT5. Kiểm tra đăng nhập và Algo Trading.', {}
            return True, f'Vị thế #{ticket} đã đóng trước đó trên sàn MT5', {'already_closed': True}

        broker = pos.symbol
        real_vol = float(pos.volume)
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        spec = cls.spec(broker)
        modes = list(spec.get('filling_modes') or [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN])
        last_err = 'MT5 từ chối đóng lệnh'
        with _LOCK:
            for _ in range(3):
                tick = mt5.symbol_info_tick(broker)
                if not tick:
                    return False, f'Chưa có tick {broker} để đóng lệnh.', {}
                price = float(tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask)
                for f_mode in modes:
                    req = {
                        'action': mt5.TRADE_ACTION_DEAL,
                        'position': int(ticket),
                        'symbol': broker,
                        'volume': real_vol,
                        'type': close_type,
                        'price': price,
                        'deviation': 50,
                        'magic': int(getattr(pos, 'magic', 0) or 0),
                        'comment': 'Close',
                        'type_time': mt5.ORDER_TIME_GTC,
                        'type_filling': f_mode,
                    }
                    res = mt5.order_send(req)
                    if res is None:
                        last_err = str(mt5.last_error())
                        continue
                    if res.retcode in SUCCESS_RETCODES or res.retcode == RET_POSITION_CLOSED:
                        return True, f'Đã đóng thành công lệnh #{ticket} trên Exness MT5', {
                            'deal': str(getattr(res, 'deal', '') or ''),
                            'price': float(getattr(res, 'price', 0) or 0),
                        }
                    if res.retcode == RET_INVALID_FILL:
                        last_err = res.comment or 'Invalid filling'
                        continue
                    if res.retcode in RETRY_RETCODES:
                        last_err = res.comment or str(res.retcode)
                        time.sleep(0.05)
                        break
                    if res.retcode in (RET_AT_DISABLED_CLIENT, RET_AT_DISABLED_SERVER):
                        return False, 'Algo Trading đang tắt trên MT5. Hãy bật AutoTrading.', {}
                    last_err = f"{res.comment} (retcode {res.retcode})"
                    break
        return False, f'MT5 từ chối đóng lệnh: {last_err}', {}

    @classmethod
    def modify_sltp(cls, ticket: int, sl: float, tp: float, symbol: str = '') -> tuple[bool, str]:
        pos = cls.position_by_ticket(ticket)
        broker = pos.symbol if pos else symbol
        if not broker:
            return False, 'Không tìm thấy vị thế để dời SL/TP'
        with _LOCK:
            req = {
                'action': mt5.TRADE_ACTION_SLTP,
                'position': int(ticket),
                'symbol': broker,
                'sl': float(sl) if sl else 0.0,
                'tp': float(tp) if tp else 0.0,
            }
            res = mt5.order_send(req)
            if res and res.retcode in SUCCESS_RETCODES | {RET_NO_CHANGES}:
                return True, f'Đã dời SL ({sl}) và TP ({tp}) thành công cho lệnh #{ticket}'
            err = res.comment if res else str(mt5.last_error())
            return False, f'Lỗi MT5 dời SL/TP: {err}'
