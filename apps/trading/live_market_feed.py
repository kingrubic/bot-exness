import os
import json
import time
import logging
import urllib.request
from urllib.error import HTTPError
from decimal import Decimal
from django.utils import timezone
from apps.symbols.models import SymbolConfig

logger = logging.getLogger(__name__)

# Try importing MetaTrader5 if available (Windows VPS with Exness MT5 Terminal)
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False


class LiveMarketFeedService:
    """
    Dịch vụ đồng bộ giá thị trường trực tiếp 100% thời gian thực từ sàn Exness:
    1. Khi chạy trên Windows VPS có cài MT5: Kết nối trực tiếp Exness MT5 Terminal (`symbol_info_tick`).
    2. Khi chạy trên Linux / Server: Sử dụng luồng cấp giá Broker Institutional Feed (TradingView + Binance)
       để lấy chính xác từng giá Bid, Ask, Spread và Last Price của thị trường tài chính trực tiếp.
    """

    _last_fetch_time = 0
    _cached_prices = {}
    _last_db_flush = 0.0
    _last_mt5_poll = 0.0
    _tv_next_ok = 0.0
    _tv_429_until = 0.0
    _tv_429_logged = False
    TV_MIN_INTERVAL_SEC = 15.0
    TV_429_COOLDOWN_SEC = 90.0
    MT5_POLL_MIN_SEC = 0.25

    TV_BROKER_MAPPINGS = {
        # Forex Major / Minor (Exness ECN / Pro Spreads)
        'EURUSD': ('forex', 'FX:EURUSD'),
        'GBPUSD': ('forex', 'FX:GBPUSD'),
        'USDJPY': ('forex', 'FX:USDJPY'),
        'AUDUSD': ('forex', 'FX:AUDUSD'),
        'USDCAD': ('forex', 'FX:USDCAD'),
        'USDCHF': ('forex', 'FX:USDCHF'),
        'NZDUSD': ('forex', 'FX:NZDUSD'),
        'EURJPY': ('forex', 'FX:EURJPY'),
        'GBPJPY': ('forex', 'FX:GBPJPY'),
        'EURGBP': ('forex', 'FX:EURGBP'),
        'AUDJPY': ('forex', 'FX:AUDJPY'),
        
        # Metals (Spot Gold & Silver)
        'XAUUSD': ('cfd', 'FX_IDC:XAUUSD'),
        'XAUEUR': ('cfd', 'OANDA:XAUEUR'),
        'XAGUSD': ('cfd', 'FX_IDC:XAGUSD'),
        'XPTUSD': ('cfd', 'TVC:PLATINUM'),
        
        # Energy & Commodities
        'USOIL': ('cfd', 'TVC:USOIL'),
        'UKOIL': ('cfd', 'TVC:UKOIL'),
        'XNGUSD': ('cfd', 'TVC:NATGAS'),
        
        # Global Indices
        'US30': ('cfd', 'TVC:DJI'),
        'US500': ('cfd', 'TVC:SPX'),
        'USTEC': ('cfd', 'TVC:IXIC'),
        'DE40': ('cfd', 'TVC:DAX'),
        
        # Crypto
        'BTCUSD': ('crypto', 'BINANCE:BTCUSDT'),
        'ETHUSD': ('crypto', 'BINANCE:ETHUSDT'),
        'SOLUSD': ('crypto', 'BINANCE:SOLUSDT'),
        'BNBUSD': ('crypto', 'BINANCE:BNBUSDT'),
    }

    @classmethod
    def fetch_tradingview_broker_feed(cls, target_symbols: list) -> dict:
        """
        Lấy giá Bid/Ask/Close thời gian thực từ luồng Broker Scanner.
        """
        results = {}
        scanners = {'forex': [], 'cfd': [], 'crypto': []}
        
        # Phân loại các cặp cần quét
        for sym_name in target_symbols:
            if sym_name in cls.TV_BROKER_MAPPINGS:
                stype, ticker_code = cls.TV_BROKER_MAPPINGS[sym_name]
                scanners[stype].append((sym_name, ticker_code))

        for scanner_type, pairs in scanners.items():
            if not pairs:
                continue
            
            ticker_list = [p[1] for p in pairs]
            lookup = {p[1]: p[0] for p in pairs}
            
            url = f"https://scanner.tradingview.com/{scanner_type}/scan"
            payload = json.dumps({
                "symbols": {"tickers": ticker_list},
                "columns": ["close", "bid", "ask", "change"]
            }).encode('utf-8')

            now = time.time()
            if now < cls._tv_429_until or now < cls._tv_next_ok:
                continue
            try:
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Content-Type': 'application/json'
                    }
                )
                with urllib.request.urlopen(req, timeout=2.5) as res:
                    data = json.loads(res.read().decode())
                    for item in data.get('data', []):
                        t_code = item.get('s')
                        d = item.get('d', [])
                        if t_code in lookup and len(d) >= 3:
                            our_sym = lookup[t_code]
                            close_p = float(d[0]) if d[0] is not None else None
                            bid_p = float(d[1]) if d[1] is not None else close_p
                            ask_p = float(d[2]) if d[2] is not None else close_p
                            
                            if close_p is not None:
                                results[our_sym] = {
                                    'price': close_p,
                                    'bid': bid_p or close_p,
                                    'ask': ask_p or close_p
                                }
                    cls._tv_429_logged = False
                    cls._tv_next_ok = time.time() + cls.TV_MIN_INTERVAL_SEC
            except HTTPError as e:
                if int(getattr(e, 'code', 0) or 0) == 429:
                    cls._tv_429_until = time.time() + cls.TV_429_COOLDOWN_SEC
                    cls._tv_next_ok = cls._tv_429_until
                    if not cls._tv_429_logged:
                        cls._tv_429_logged = True
                        logger.warning(
                            "TradingView scanner 429 — tạm ngưng %ss, dùng giá MT5/cache.",
                            int(cls.TV_429_COOLDOWN_SEC),
                        )
                else:
                    logger.debug("TradingView scanner %s error: %s", scanner_type, e)
            except Exception as e:
                logger.debug("TradingView scanner %s error: %s", scanner_type, e)

        return results

    _last_network_time = 0
    _network_feed_cache = {}
    _bg_thread_started = False

    @classmethod
    def _start_bg_feed_updater(cls):
        """Khởi động luồng nền cập nhật giá Broker Institutional ngầm để không bao giờ nghẽn WebSocket."""
        if cls._bg_thread_started:
            return
        cls._bg_thread_started = True
        import threading
        def _bg_loop():
            while True:
                try:
                    from apps.trading.mt5_connector import ExnessMT5Connector
                    from apps.trading.mt5_session import MT5_AVAILABLE as SESSION_MT5
                    # Có MT5/Wine thì không cần TradingView
                    if SESSION_MT5 or ExnessMT5Connector.is_wine_bridge_open():
                        time.sleep(5.0)
                        continue
                    now = time.time()
                    cooldown = max(cls._tv_429_until, cls._tv_next_ok) - now
                    if cooldown > 0.2:
                        time.sleep(min(cooldown, 30.0))
                        continue
                    active_names = list(SymbolConfig.objects.filter(is_active=True).values_list('symbol', flat=True))
                    if active_names:
                        fresh_feed = cls.fetch_tradingview_broker_feed(active_names)
                        if fresh_feed:
                            cls._network_feed_cache.update(fresh_feed)
                except Exception:
                    pass
                time.sleep(cls.TV_MIN_INTERVAL_SEC)
        t = threading.Thread(target=_bg_loop, daemon=True)
        t.start()

    @classmethod
    def sync_all_symbols(cls) -> dict:
        """Tick native MT5 (in-memory). Ghi DB tối đa 1 lần/giây. Không giả lập pipette."""
        active_symbols = list(SymbolConfig.objects.filter(is_active=True))
        if not active_symbols:
            return {}

        from apps.trading.mt5_session import MT5NativeSession, MT5_AVAILABLE as SESSION_MT5
        from apps.trading.mt5_connector import ExnessMT5Connector, BRIDGE_URL

        names = [s.symbol for s in active_symbols]
        mt5_updated = {}

        if SESSION_MT5:
            ticks = MT5NativeSession.snapshot_ticks(names)
            if ticks:
                cls._cached_prices.update(ticks)
                mt5_updated = {k: float(v['last']) for k, v in ticks.items()}
                cls._flush_ticks_to_db(active_symbols, ticks)
                return mt5_updated

        if ExnessMT5Connector.is_wine_bridge_open():
            now = time.time()
            if now - cls._last_mt5_poll < cls.MT5_POLL_MIN_SEC and cls._cached_prices:
                return {k: float(v.get('last') or 0) for k, v in cls._cached_prices.items()}
            try:
                import requests
                resp = requests.get(f"{BRIDGE_URL}/all_prices", timeout=0.8)
                if resp.status_code == 200 and resp.json().get('success'):
                    mt5_prices = resp.json().get('prices', {})
                    tick_map = {}
                    for sym in active_symbols:
                        price_info = None
                        for cand in ExnessMT5Connector.symbol_candidates(sym.symbol):
                            price_info = mt5_prices.get(cand)
                            if price_info:
                                break
                        if not price_info:
                            continue
                        last = price_info.get('last') or price_info.get('bid')
                        if last is None:
                            continue
                        tick_map[sym.symbol] = {
                            'last': float(last),
                            'bid': float(price_info.get('bid') or last),
                            'ask': float(price_info.get('ask') or last),
                            'spread_pips': float(price_info.get('spread_pips') or 0),
                        }
                    if tick_map:
                        cls._last_mt5_poll = now
                        cls._cached_prices.update(tick_map)
                        cls._flush_ticks_to_db(active_symbols, tick_map)
                        return {k: v['last'] for k, v in tick_map.items()}
            except Exception as me:
                logger.debug(f"MT5 all_prices fetch error: {me}")
            # Bridge đang mở: không fallback TradingView (tránh 429). Dùng tick MT5 cache.
            if cls._cached_prices:
                return {k: float(v.get('last') or 0) for k, v in cls._cached_prices.items()}
            return {s.symbol: float(s.current_price or 0) for s in active_symbols}

        cls._start_bg_feed_updater()
        broker_feed = cls._network_feed_cache
        fallback = {}
        for sym in active_symbols:
            if sym.symbol in broker_feed:
                data = broker_feed[sym.symbol]
                fallback[sym.symbol] = {
                    'last': float(data['price']),
                    'bid': float(data.get('bid') or data['price']),
                    'ask': float(data.get('ask') or data['price']),
                    'spread_pips': float(sym.current_spread_pips or 0),
                }
        if fallback:
            cls._cached_prices.update(fallback)
            cls._flush_ticks_to_db(active_symbols, fallback)
            return {k: v['last'] for k, v in fallback.items()}
        return {s.symbol: float(s.current_price or 0) for s in active_symbols}

    @classmethod
    def _flush_ticks_to_db(cls, active_symbols, ticks: dict):
        now = time.time()
        if now - cls._last_db_flush < 1.0:
            return
        cls._last_db_flush = now
        changed = []
        for sym in active_symbols:
            data = ticks.get(sym.symbol)
            if not data:
                continue
            digits = int(sym.digits or 5)
            last = round(float(data['last']), digits)
            bid = round(float(data.get('bid') or last), digits)
            ask = round(float(data.get('ask') or last), digits)
            spread = float(data.get('spread_pips') or 0)
            if (
                float(sym.current_price or 0) == last
                and float(sym.current_bid or 0) == bid
                and float(sym.current_ask or 0) == ask
            ):
                continue
            sym.current_price = Decimal(str(last))
            sym.current_bid = Decimal(str(bid))
            sym.current_ask = Decimal(str(ask))
            sym.current_spread_pips = spread
            sym.last_scanned_at = timezone.now()
            changed.append(sym)
        if changed:
            SymbolConfig.objects.bulk_update(
                changed,
                ['current_price', 'current_bid', 'current_ask', 'current_spread_pips', 'last_scanned_at'],
            )
