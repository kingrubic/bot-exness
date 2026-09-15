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
from datetime import datetime, timedelta, timezone as dt_timezone
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
    def login(cls, login: int, password: str, server: str, allow_switch: bool = False) -> bool:
        """Gắn IPC vào tài khoản đang mở. Không đổi login trên terminal trừ khi allow_switch=True."""
        if not cls.ensure():
            return False
        with _LOCK:
            acc = mt5.account_info()
            if acc and int(acc.login) == int(login):
                cls._login = int(login)
                return True
            if acc and int(acc.login) != int(login) and not allow_switch:
                logger.warning(
                    "MT5 terminal đang #%s (%s), bỏ qua login #%s — 1 cửa sổ MT5 = 1 tài khoản.",
                    acc.login, getattr(acc, 'server', ''), login,
                )
                return False
            if not password:
                return bool(acc and int(acc.login) == int(login))
            ok = bool(mt5.login(login=int(login), password=password, server=server or ''))
            if ok:
                cls._login = int(login)
            else:
                logger.warning("MT5 login #%s failed: %s", login, mt5.last_error())
            return ok

    @staticmethod
    def explain_single_account(live_login, live_server, requested_login) -> str:
        return (
            f"Một cửa sổ MetaTrader 5 chỉ đăng nhập được 1 tài khoản. "
            f"Terminal đang mở #{live_login} ({live_server or 'Exness'}). "
            f"Không thể kết nối ví #{requested_login} cùng lúc trên cùng terminal. "
            f"Cách làm: (1) Dùng ví #{live_login} trên web, hoặc (2) trong MT5: File → Login to Trade Account "
            f"sang #{requested_login} (ví kia sẽ ngắt), hoặc (3) cài thêm bản MT5 portable cho tài khoản thứ hai."
        )

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

    @staticmethod
    def deal_int(deal, field: str, default: int = -1) -> int:
        """MT5 DEAL_TYPE_BUY = 0; không dùng `or default` vì 0 bị thành default."""
        val = getattr(deal, field, None)
        if val is None:
            return default
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    @classmethod
    def group_closed_deals(cls, deals) -> list[dict]:
        """Gom deal MT5 theo position_id thành lệnh đã đóng. DB chỉ dùng để gắn BOT/USER."""
        if not deals:
            return []
        try:
            from apps.trading.order_source import classify_order_source
        except Exception:
            classify_order_source = None

        by_pos: dict[str, dict] = {}
        for d in deals:
            deal_type = cls.deal_int(d, 'type', -1)
            if deal_type not in (0, 1):
                continue
            pos_id_raw = cls.deal_int(d, 'position_id', 0)
            if pos_id_raw <= 0:
                continue
            pos_id = str(pos_id_raw)
            grp = by_pos.setdefault(pos_id, {'in': None, 'outs': [], 'all': []})
            grp['all'].append(d)
            entry = cls.deal_int(d, 'entry', 0)
            if entry == 0:
                if grp['in'] is None:
                    grp['in'] = d
            elif entry in (1, 2, 3):
                grp['outs'].append(d)

        rows = []
        for pos_id, grp in by_pos.items():
            outs = grp['outs']
            if not outs:
                continue
            in_deal = grp['in']
            out_deal = outs[-1]
            target = out_deal or in_deal
            raw_sym = str(getattr(target, 'symbol', '') or '').strip()
            if not raw_sym:
                continue
            sym = cls.normalize_symbol(raw_sym) or raw_sym

            prices = [float(getattr(x, 'price', 0) or 0) for x in grp['all'] if float(getattr(x, 'price', 0) or 0) > 0]
            if in_deal:
                deal_type = 'BUY' if int(in_deal.type) == 0 else 'SELL'
                open_p = float(in_deal.price or 0)
                open_ts = int(in_deal.time or 0)
            else:
                deal_type = 'BUY' if int(out_deal.type) == 1 else 'SELL'
                open_p = prices[0] if prices else float(out_deal.price or 0)
                open_ts = int(getattr(grp['all'][0], 'time', 0) or out_deal.time or 0)
            close_p = float(out_deal.price or 0)
            close_ts = int(out_deal.time or 0)
            if open_p <= 0 and prices:
                open_p = prices[0]
            if close_p <= 0 and prices:
                close_p = prices[-1]

            total_profit = float(sum(float(getattr(x, 'profit', 0) or 0) for x in grp['all']))
            total_comm = float(sum(float(getattr(x, 'commission', 0) or 0) for x in grp['all']))
            total_swap = float(sum(float(getattr(x, 'swap', 0) or 0) for x in grp['all']))
            net = round(total_profit + total_comm + total_swap, 2)
            lot = round(sum(float(getattr(x, 'volume', 0) or 0) for x in outs), 2) or float(getattr(out_deal, 'volume', 0) or 0)
            comment = str(getattr(target, 'comment', '') or '').strip()
            magic = int(getattr(target, 'magic', 0) or 0)
            if classify_order_source:
                source = classify_order_source(magic, comment, pos_id)
            else:
                source = 'BOT' if magic == 8882026 else 'USER'
            open_dt = datetime.fromtimestamp(open_ts, tz=dt_timezone.utc) if open_ts else datetime.now(dt_timezone.utc)
            close_dt = datetime.fromtimestamp(close_ts, tz=dt_timezone.utc) if close_ts else open_dt
            rows.append({
                'ticket': pos_id,
                'symbol': sym,
                'position_type': deal_type,
                'lot_size': lot,
                'open_price': open_p,
                'close_price': close_p,
                'pnl': net,
                'commission': round(total_comm, 2),
                'swap': round(total_swap, 2),
                'pips': 0.0,
                'close_reason': 'MANUAL_CLOSE' if source == 'USER' else 'TP_HIT',
                'is_win': net > 0,
                'source': source,
                'magic': magic,
                'comment': comment,
                'opened_at': open_dt,
                'closed_at': close_dt,
            })
        rows.sort(key=lambda r: r['closed_at'], reverse=True)
        return rows

    _hist_cache = {'ts': 0.0, 'rows': None}

    @classmethod
    def closed_history(cls, days: int = 90, limit: int = 200) -> list[dict]:
        """Lịch sử lệnh đã đóng từ history_deals MT5 (cache 2s)."""
        empty: list[dict] = []
        if not cls.available() or not cls.ensure():
            return empty
        now = time.time()
        cached = cls._hist_cache
        if cached.get('rows') is not None and (now - cached.get('ts', 0)) < 2.0:
            return cached['rows'][:limit]
        date_from = datetime.now() - timedelta(days=max(1, int(days)))
        deals = cls.history_deals(date_from, datetime.now())
        if deals is None:
            return cached.get('rows') or empty
        rows = cls.group_closed_deals(deals)
        cls._hist_cache = {'ts': now, 'rows': rows}
        return rows[:limit]

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

    @staticmethod
    def summarize_today_deals(deals, start_ts: float) -> dict:
        """
        all: lãi/lỗ deal BUY/SELL cả ngày (không gồm nạp/rút).
        risk: lãi/lỗ giao dịch sau lần nạp/credit dương gần nhất trong ngày
              (sau thanh lý nạp lại thì risk ≈ 0, bot được đánh tiếp).
        """
        empty = {'all': 0.0, 'BOT': 0.0, 'USER': 0.0, 'risk': 0.0, 'ok': True}
        if not deals:
            return empty
        try:
            from apps.trading.order_source import classify_order_source
        except Exception:
            classify_order_source = None

        last_reset_ts = float(start_ts)
        for d in deals:
            ts = int(getattr(d, 'time', 0) or 0)
            if ts < start_ts:
                continue
            deal_type = MT5NativeSession.deal_int(d, 'type', -1)
            # BALANCE=2, CREDIT=3, CORRECTION=5, BONUS=6
            if deal_type in (2, 3, 5, 6) and float(getattr(d, 'profit', 0) or 0) > 0:
                last_reset_ts = max(last_reset_ts, float(ts))

        bot = 0.0
        user = 0.0
        risk = 0.0
        for d in deals:
            deal_type = MT5NativeSession.deal_int(d, 'type', -1)
            if deal_type not in (0, 1):
                continue
            ts = int(getattr(d, 'time', 0) or 0)
            if ts < start_ts:
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
            if ts > last_reset_ts:
                risk += net
        return {
            'all': round(bot + user, 2),
            'BOT': round(bot, 2),
            'USER': round(user, 2),
            'risk': round(risk, 2),
            'ok': True,
        }

    @classmethod
    def today_realized_pnl(cls) -> dict:
        """Lãi/lỗ đã đóng trong ngày (local midnight) từ history_deals MT5, không lấy TradeHistory DB."""
        empty = {'all': 0.0, 'BOT': 0.0, 'USER': 0.0, 'risk': 0.0, 'ok': False}
        if not cls.available() or not cls.ensure():
            return empty
        now_dt = datetime.now()
        day = now_dt.date()
        now = time.time()
        cached = cls._today_pnl_cache
        if cached.get('data') and cached.get('day') == day and (now - cached.get('ts', 0)) < 3.0:
            return cached['data']
        date_from = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        start_ts = date_from.timestamp()
        # Lấy rộng hơn 12h rồi lọc theo unix local midnight — khớp History Today, không lệch timezone API.
        deals = cls.history_deals(date_from - timedelta(hours=12), now_dt)
        if deals is None:
            return empty
        data = cls.summarize_today_deals(deals, start_ts)
        data['ok'] = True
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
        if not cls.ensure():
            return False, 'Chưa kết nối sàn Exness MT5. Hãy mở MT5, đăng nhập và bật Algo Trading.', {}
        pos = cls.position_by_ticket(ticket)
        if pos is None:
            rows = cls.positions()
            if rows is None:
                return False, 'Không lấy được danh sách vị thế từ MT5. Kiểm tra đăng nhập và Algo Trading.', {'retryable': True}
            return True, f'Vị thế #{ticket} đã đóng trước đó trên sàn MT5', {'already_closed': True}

        broker = pos.symbol
        real_vol = float(pos.volume)
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        spec = cls.spec(broker)
        modes = list(spec.get('filling_modes') or [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN])
        last_err = 'MT5 từ chối đóng lệnh'
        last_code = 0
        with _LOCK:
            for _ in range(6):
                tick = mt5.symbol_info_tick(broker)
                if not tick:
                    return False, f'Chưa có tick {broker} để đóng lệnh.', {'retryable': True}
                price = float(tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask)
                for f_mode in modes:
                    req = {
                        'action': mt5.TRADE_ACTION_DEAL,
                        'position': int(ticket),
                        'symbol': broker,
                        'volume': real_vol,
                        'type': close_type,
                        'price': price,
                        'deviation': 300,
                        'magic': int(getattr(pos, 'magic', 0) or 0),
                        'comment': 'Close',
                        'type_time': mt5.ORDER_TIME_GTC,
                        'type_filling': f_mode,
                    }
                    res = mt5.order_send(req)
                    if res is None:
                        last_err = str(mt5.last_error())
                        last_code = 0
                        continue
                    last_code = int(res.retcode)
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
                        time.sleep(0.08)
                        break
                    if res.retcode in (RET_AT_DISABLED_CLIENT, RET_AT_DISABLED_SERVER):
                        return False, 'Algo Trading đang tắt trên MT5. Hãy bật AutoTrading.', {'retryable': False}
                    if res.retcode == RET_MARKET_CLOSED:
                        return False, f'Thị trường {broker} đang đóng.', {'retryable': False}
                    last_err = f"{res.comment} (retcode {res.retcode})"
                    return False, f'MT5 từ chối đóng lệnh: {last_err}', {'retryable': False, 'retcode': last_code}
                else:
                    continue
        still = cls.position_by_ticket(ticket)
        if still is None:
            return True, f'Vị thế #{ticket} đã đóng trên sàn MT5', {'already_closed': True}
        retryable = last_code in RETRY_RETCODES
        return False, f'MT5 từ chối đóng lệnh: {last_err}', {'retryable': retryable, 'retcode': last_code}

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
