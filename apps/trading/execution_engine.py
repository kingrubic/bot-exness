import logging
import time
import traceback
import uuid
from decimal import Decimal
from django.utils import timezone
from apps.accounts.models import WalletAccount
from apps.symbols.models import SymbolConfig
from apps.analysis.models import MarketForecast
from apps.analysis.analyzer import TechnicalAnalyzer
from apps.plans.models import TradingPlan
from apps.plans.planner import AutoPlanGenerator
from apps.trading.models import Position, TradeHistory, BotLog
from apps.trading.mt5_connector import ExnessMT5Connector

logger = logging.getLogger(__name__)

class ExecutionEngine:
    """
    Thực thi sau khi kiểm tra lại tín hiệu, giá và rủi ro.
    SL bảo vệ gửi cùng lệnh; TP ròng và trailing theo cấu hình ví.
    """
    _last_persist = 0.0
    _close_cooldown: dict[str, float] = {}
    _closing: set[str] = set()

    @classmethod
    def sync_symbol_prices_from_mt5(cls):
        """Đồng bộ giá Bid/Ask/Spread thực tế 100% từ MT5 và Real Financial Feeds."""
        try:
            from apps.trading.live_market_feed import LiveMarketFeedService
            LiveMarketFeedService.sync_all_symbols()
        except Exception as e:
            logger.warning(f"Lỗi khi đồng bộ giá thị trường thực tế: {e}")

    @classmethod
    def trigger_plan_to_position(cls, plan: TradingPlan) -> Position:
        """Kích hoạt Trading Plan (Hỗ trợ Live MT5 cho ví Real và Sandbox Engine cho ví Demo)."""
        wallet = plan.wallet
        if not wallet or not cls.wallet_can_autotrade(wallet):
            AutoPlanGenerator.discard_plan(plan)
            return None
        if not AutoPlanGenerator.wallet_has_capital(wallet):
            AutoPlanGenerator.purge_all_plans_for_wallet(wallet)
            return None
        if cls.count_open_positions(wallet) >= cls.get_max_allowed_positions_for_wallet(wallet):
            AutoPlanGenerator.purge_pending_plans(wallet)
            AutoPlanGenerator.discard_plan(plan)
            return None

        is_demo = (wallet.account_type in ['DEMO', 'SIMULATION']) or ('Trial' in wallet.mt5_server) or ('Demo' in wallet.mt5_server)
        connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
        connected_mt5 = connector.connect(allow_switch=True)

        # Nếu là ví Real mà không kết nối được MT5 -> hủy plan, không giữ FAILED
        if not is_demo and not connected_mt5:
            BotLog.log(
                level='ERROR',
                category='EXECUTION',
                wallet=wallet,
                symbol=plan.symbol,
                message=f"Không thể kết nối tới Exness MT5 Live để mở lệnh {plan.direction} {plan.symbol} cho ví Real '{wallet.name}' (#{wallet.mt5_login}). Đã xóa kế hoạch thất bại.",
                traceback=f"MT5 connect failed on server {wallet.mt5_server} for wallet #{wallet.mt5_login}"
            )
            AutoPlanGenerator.discard_plan(plan)
            return None

        # Luôn lấy giá MARKET mới nhất (Ask/Bid) ngay trước khi gửi lệnh
        sym_live = AutoPlanGenerator.refresh_symbol_market_price(plan.symbol)
        forecast = MarketForecast.objects.filter(symbol=plan.symbol).order_by('-updated_at', '-id').first()
        if not sym_live or not forecast or AutoPlanGenerator.direction_from_forecast(forecast) != plan.direction:
            AutoPlanGenerator.discard_plan(plan)
            return None
        wallet.refresh_from_db()
        if not cls.wallet_can_autotrade(wallet):
            AutoPlanGenerator.discard_plan(plan)
            return None
        fields = AutoPlanGenerator._plan_fields(wallet, sym_live, forecast)
        if not fields:
            AutoPlanGenerator.discard_plan(plan)
            return None
        for field in ('entry_price', 'entry_zone_low', 'entry_zone_high', 'stop_loss',
                      'take_profit_1', 'take_profit_2', 'rr_ratio', 'risk_amount_usd',
                      'calculated_lot', 'rationale'):
            setattr(plan, field, fields[field])
        plan.save()

        if connected_mt5:
            order_res = connector.send_order(
                symbol=plan.symbol,
                order_type=plan.direction,
                volume=float(plan.calculated_lot),
                price=0.0,
                sl=float(plan.stop_loss),
                tp=float(plan.take_profit_2 or plan.take_profit_1 or 0),
                comment=f"AI-{plan.symbol}"
            )

            if not order_res.get('success'):
                BotLog.log(
                    level='ERROR',
                    category='EXECUTION',
                    wallet=wallet,
                    symbol=plan.symbol,
                    message=f"Khớp lệnh THẬT thất bại trên sàn Exness MT5: {order_res.get('error')}. Đã xóa kế hoạch thất bại.",
                    traceback=str(order_res)
                )
                AutoPlanGenerator.discard_plan(plan)
                return None

            ticket = order_res.get('ticket')
            exec_price = Decimal(str(order_res.get('price', plan.entry_price)))
            exec_volume = float(order_res.get('volume', plan.calculated_lot))
            from apps.core.trading_defaults import BOT_MAGIC
            from apps.trading.order_source import remember_order_source
            remember_order_source(ticket, 'BOT', BOT_MAGIC)
            sl_to_save = plan.stop_loss
            position = Position.objects.filter(ticket=str(ticket)).first()
            if not position:
                position = Position.objects.create(
                    wallet=wallet,
                    plan=plan,
                    ticket=str(ticket),
                    symbol=plan.symbol,
                    position_type=plan.direction,
                    lot_size=exec_volume,
                    open_price=exec_price,
                    current_price=exec_price,
                    stop_loss=sl_to_save,
                    take_profit=plan.take_profit_2 or plan.take_profit_1,
                    source='BOT',
                    magic=BOT_MAGIC,
                    comment=f"AI-{plan.symbol}",
                    opened_at=timezone.now(),
                )
            else:
                position.plan = plan
                position.source = 'BOT'
                position.magic = BOT_MAGIC
                position.stop_loss = sl_to_save
                position.take_profit = plan.take_profit_2 or plan.take_profit_1
                position.save(update_fields=['plan', 'source', 'magic', 'stop_loss', 'take_profit'])
            plan.status = 'EXECUTING'
            plan.triggered_at = timezone.now()
            plan.save()
            BotLog.log(
                level='INFO',
                category='EXECUTION',
                wallet=wallet,
                symbol=plan.symbol,
                message=f"Đã mở lệnh BOT trên Exness MT5: {plan.direction} {exec_volume} {plan.symbol} Ticket #{ticket}"
            )
            return position
        else:
            if wallet.mt5_login:
                AutoPlanGenerator.discard_plan(plan)
                BotLog.log(
                    level='WARNING',
                    category='EXECUTION',
                    wallet=wallet,
                    symbol=plan.symbol,
                    message=f"Chưa gửi '{plan.symbol}': MT5 chưa kết nối. Không giữ plan chờ (sẽ lập lại khi còn slot và MT5 sẵn sàng).",
                )
                return None
            else:
                # Chỉ khi ví hoàn toàn không cấu hình MT5 (ví mô phỏng nội bộ)
                ticket = f"LOCAL-{time.time_ns()}-{uuid.uuid4().hex[:6]}"
                exec_price = plan.entry_price
                exec_volume = float(plan.calculated_lot)

        sl_to_save = cls.compute_stop_loss_price(
            plan.symbol, plan.direction, exec_price, exec_volume, wallet
        )
        if plan.stop_loss:
            sl_to_save = plan.stop_loss

        position = Position.objects.create(
            wallet=wallet,
            plan=plan,
            ticket=ticket,
            symbol=plan.symbol,
            position_type=plan.direction,
            lot_size=exec_volume,
            open_price=exec_price,
            current_price=exec_price,
            stop_loss=sl_to_save,
            take_profit=plan.take_profit_2 or plan.take_profit_1,
            floating_pnl=Decimal("0.00"),
            floating_pips=0.0,
            source='BOT',
            magic=8882026,
            comment=f"AI-{plan.symbol}",
            is_trailing=True,
            opened_at=timezone.now()
        )
        plan.status = 'EXECUTING'
        plan.triggered_at = timezone.now()
        plan.save()

        # Ghi log thành công
        BotLog.log(
            level='INFO',
            category='EXECUTION',
            wallet=wallet,
            symbol=plan.symbol,
            message=f"Đã mở vị thế [{position.source}] #{position.ticket} ({plan.direction} {position.lot_size} Lot {plan.symbol}) tại giá {position.open_price}"
        )

        return position

    @classmethod
    def count_open_positions(cls, wallet: WalletAccount) -> int:
        """Đếm lệnh đang mở: max(MT5 live, DB) để không lập plan khi đã đủ lệnh."""
        db_n = 0
        try:
            db_n = int(wallet.positions.count())
        except Exception:
            db_n = 0
        live_n = None
        try:
            from apps.trading.mt5_session import MT5NativeSession
            if wallet.mt5_login and MT5NativeSession.available():
                acc = MT5NativeSession.account()
                if acc and str(acc.login) == str(wallet.mt5_login):
                    rows = MT5NativeSession.positions()
                    if rows is not None:
                        live_n = len(rows)
        except Exception:
            live_n = None
        if live_n is None:
            return db_n
        return max(live_n, db_n)

    @classmethod
    def get_max_allowed_positions_for_wallet(cls, wallet: WalletAccount) -> int:
        """Số lệnh mở đồng thời = cấu hình ví (trần an toàn 500)."""
        try:
            n = int(wallet.max_open_trades or 5)
        except (TypeError, ValueError):
            n = 5
        return max(1, min(n, 500))

    @classmethod
    def wallet_min_take_profit_usd(cls, wallet: WalletAccount) -> float:
        """0 = không tự chốt lời."""
        try:
            raw = getattr(wallet, 'min_take_profit_usd', None)
            if raw is None or str(raw).strip() == '':
                return 0.0
            v = float(raw)
        except (TypeError, ValueError):
            return 0.0
        return round(v, 2) if v > 0 else 0.0

    @classmethod
    def wallet_max_stop_loss_usd(cls, wallet: WalletAccount) -> float:
        """0 = không tự cắt lỗ theo USD."""
        try:
            raw = getattr(wallet, 'max_stop_loss_usd', None)
            if raw is None or str(raw).strip() == '':
                return 0.0
            v = float(raw)
        except (TypeError, ValueError):
            return 0.0
        return round(v, 2) if v > 0 else 0.0

    @classmethod
    def wallet_trail_sl_lock_usd(cls, wallet: WalletAccount) -> float:
        """0 = tắt dời SL. Ví dụ 1: lãi 2$ → SL còn 1$; lãi 3$ → SL còn 2$."""
        if not bool(getattr(wallet, 'trail_sl_enabled', False)):
            return 0.0
        try:
            raw = getattr(wallet, 'trail_sl_lock_usd', None)
            if raw is None or str(raw).strip() == '':
                return 0.0
            v = float(raw)
        except (TypeError, ValueError):
            return 0.0
        return round(v, 2) if v > 0 else 0.0

    @classmethod
    def wallet_max_loss_usd(cls, wallet: WalletAccount) -> float:
        return cls.wallet_max_stop_loss_usd(wallet)

    @classmethod
    def wallet_today_risk_pnl(cls, wallet: WalletAccount) -> float:
        """Lãi/lỗ đã đóng hôm nay (sau lần nạp dương gần nhất nếu có)."""
        try:
            from apps.trading.mt5_session import MT5NativeSession
            if wallet.mt5_login and MT5NativeSession.available():
                acc = MT5NativeSession.account()
                if acc and str(acc.login) == str(wallet.mt5_login):
                    snap = MT5NativeSession.today_realized_pnl()
                    if snap.get('ok'):
                        if 'risk' in snap:
                            return float(snap['risk'])
                        return float(snap.get('all') or 0)
        except Exception:
            pass
        return float(wallet.get_today_pnl() or 0)

    @classmethod
    def wallet_scalp_loss_usd(cls, wallet: WalletAccount) -> float:
        return cls.wallet_max_stop_loss_usd(wallet)

    @classmethod
    def auto_close_reason_for_pnl(cls, wallet: WalletAccount, pnl: float) -> str | None:
        """TP_HIT / SL_HIT / None. Ngưỡng trống = không tự đóng theo phía đó."""
        try:
            net = float(pnl or 0)
        except (TypeError, ValueError):
            net = 0.0
        min_tp = cls.wallet_min_take_profit_usd(wallet)
        if min_tp > 0 and net >= min_tp:
            return 'TP_HIT'
        max_sl = cls.wallet_max_stop_loss_usd(wallet)
        if max_sl > 0 and net <= -max_sl:
            return 'SL_HIT'
        return None

    @classmethod
    def sl_price_for_locked_profit(cls, symbol_name, direction, open_price, lot, locked_usd: float):
        """Giá SL sao cho nếu chạm thì còn locked_usd lãi."""
        if locked_usd <= 0 or open_price is None:
            return None
        sym = SymbolConfig.objects.filter(symbol=symbol_name).first()
        if not sym:
            return None
        cs = float(sym.contract_size or 100.0)
        lot_f = float(lot or 0.01)
        if cs * lot_f <= 0:
            return None
        dist = float(locked_usd) / (cs * lot_f)
        digits = int(getattr(sym, 'digits', 5) or 5)
        open_p = float(open_price)
        raw = (open_p + dist) if str(direction).upper() == 'BUY' else (open_p - dist)
        if raw <= 0:
            return None
        return Decimal(str(round(raw, digits)))

    @classmethod
    def locked_profit_from_sl(cls, symbol_name, direction, open_price, lot, sl) -> float:
        if sl is None or open_price is None:
            return 0.0
        try:
            slf = float(sl)
            open_p = float(open_price)
        except (TypeError, ValueError):
            return 0.0
        if slf <= 0:
            return 0.0
        sym = SymbolConfig.objects.filter(symbol=symbol_name).first()
        cs = float(getattr(sym, 'contract_size', 100) or 100.0) if sym else 100.0
        lot_f = float(lot or 0.01)
        if cs * lot_f <= 0:
            return 0.0
        if str(direction).upper() == 'BUY':
            locked = (slf - open_p) * cs * lot_f
        else:
            locked = (open_p - slf) * cs * lot_f
        return round(locked, 2) if locked > 0 else 0.0

    @classmethod
    def sl_price_for_lock_from_current(cls, symbol_name, direction, current_price, lot, lock_usd: float):
        """Giá SL cách giá hiện tại đúng lock_usd — luôn đúng phía thị trường (BUY dưới Bid, SELL trên Ask)."""
        if lock_usd <= 0 or current_price is None:
            return None
        sym = SymbolConfig.objects.filter(symbol=symbol_name).first()
        if not sym:
            return None
        cs = float(sym.contract_size or 100.0)
        lot_f = float(lot or 0.01)
        if cs * lot_f <= 0:
            return None
        dist = float(lock_usd) / (cs * lot_f)
        point = float(getattr(sym, 'point_size', 0) or 0) or 0.01
        dist = max(dist, point * 10.0)
        digits = int(getattr(sym, 'digits', 5) or 5)
        curr = float(current_price)
        if curr <= 0:
            return None
        raw = (curr - dist) if str(direction).upper() == 'BUY' else (curr + dist)
        if raw <= 0:
            return None
        return Decimal(str(round(raw, digits)))

    @classmethod
    def sl_is_valid_side(cls, direction, sl_price, current_price) -> bool:
        try:
            slf = float(sl_price)
            curr = float(current_price or 0)
        except (TypeError, ValueError):
            return False
        if slf <= 0 or curr <= 0:
            return False
        if str(direction).upper() == 'BUY':
            return slf < curr
        return slf > curr

    @classmethod
    def push_sl_to_broker(cls, position: Position, wallet: WalletAccount, sl_price, connector=None) -> bool:
        """Gửi SL lên MT5. LOCAL = True (chỉ DB). Lệnh thật phải modify thành công."""
        ticket = str(position.ticket or '')
        if not ticket or ticket.startswith('LOCAL-') or not wallet.mt5_login:
            return True
        if not getattr(wallet, 'is_active', False):
            return False
        try:
            slf = float(sl_price)
        except (TypeError, ValueError):
            return False
        if slf <= 0:
            return False
        try:
            conn = connector
            if conn is None:
                conn = ExnessMT5Connector(
                    login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server
                )
            if not conn.is_connected and not conn.connect(allow_switch=bool(getattr(wallet, 'is_active', False))):
                logger.warning("Không kết nối MT5 để dời SL #%s", ticket)
                return False
            tp = float(position.take_profit or 0) or 0.0
            ok, msg = conn.modify_order(ticket, slf, tp, position.symbol)
            if not ok:
                logger.warning("MT5 từ chối dời SL #%s → %s: %s", ticket, slf, msg)
                return False
            return True
        except Exception as e:
            logger.warning("Lỗi dời SL MT5 #%s: %s", ticket, e)
            return False

    @classmethod
    def maybe_update_trailing_sl(cls, position: Position, pnl: float, wallet: WalletAccount, connector=None) -> bool:
        """
        Dời SL trên sàn (và DB) để khoá lãi = pnl - lock. Chỉ dời tăng khoá lãi, không giảm.
        Tính SL từ giá hiện tại (current - lock) để không đặt SL sai phía — MT5 sẽ reject.
        Ví dụ lock=1: lãi 2$ → SL còn 1$; lãi 3$ → SL còn 2$.
        """
        lock = cls.wallet_trail_sl_lock_usd(wallet)
        if lock <= 0:
            return False
        try:
            net = float(pnl or 0)
        except (TypeError, ValueError):
            net = 0.0
        # Bắt đầu khi lãi đủ 2 lần mức khoá (lock=1 → từ 2$ trở lên)
        if net + 1e-9 < (2.0 * lock):
            return False
        target_locked = round(net - lock, 2)
        if target_locked <= 0:
            return False
        current_locked = cls.locked_profit_from_sl(
            position.symbol, position.position_type, position.open_price,
            position.lot_size, position.stop_loss,
        )
        if target_locked <= current_locked + 0.009:
            return False
        curr = position.current_price or position.open_price
        new_sl = cls.sl_price_for_lock_from_current(
            position.symbol, position.position_type, curr, position.lot_size, lock,
        )
        if new_sl is None:
            new_sl = cls.sl_price_for_locked_profit(
                position.symbol, position.position_type, position.open_price,
                position.lot_size, target_locked,
            )
        if new_sl is None:
            return False
        if not cls.sl_is_valid_side(position.position_type, new_sl, curr):
            return False
        new_locked = cls.locked_profit_from_sl(
            position.symbol, position.position_type, position.open_price,
            position.lot_size, new_sl,
        )
        if new_locked <= current_locked + 0.009:
            return False
        if not cls.push_sl_to_broker(position, wallet, new_sl, connector=connector):
            return False
        position.stop_loss = new_sl
        position.is_trailing = True
        return True

    @classmethod
    def maybe_attach_protective_sl(cls, position: Position, wallet: WalletAccount, connector=None) -> bool:
        """Nếu max SL USD đã set mà lệnh chưa có SL sàn — gắn ngay để hiện trên MT5."""
        if cls.wallet_max_stop_loss_usd(wallet) <= 0:
            return False
        try:
            existing = float(position.stop_loss or 0)
        except (TypeError, ValueError):
            existing = 0.0
        if existing > 0:
            return False
        sl_px = cls.compute_stop_loss_price(
            position.symbol, position.position_type, position.open_price,
            position.lot_size, wallet,
        )
        if sl_px is None:
            return False
        curr = position.current_price or position.open_price
        if curr and not cls.sl_is_valid_side(position.position_type, sl_px, curr):
            return False
        if not cls.push_sl_to_broker(position, wallet, sl_px, connector=connector):
            return False
        position.stop_loss = sl_px
        return True

    @classmethod
    def mt5_connector_for_wallet(cls, wallet: WalletAccount, cache: dict | None = None):
        if not wallet or not getattr(wallet, 'mt5_login', None):
            return None
        if not getattr(wallet, 'is_active', False):
            return None
        key = str(wallet.mt5_login)
        if cache is not None and key in cache:
            return cache[key]
        conn = None
        try:
            c = ExnessMT5Connector(
                login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server
            )
            if c.connect(allow_switch=True):
                conn = c
        except Exception:
            conn = None
        if cache is not None:
            cache[key] = conn
        return conn

    @classmethod
    def maybe_manage_position_sl(cls, position: Position, pnl: float, wallet: WalletAccount, connector=None) -> str:
        """'closed' | 'updated' | ''."""
        if cls.maybe_close_if_trailed_sl_hit(position, pnl, wallet):
            return 'closed'
        if cls.maybe_update_trailing_sl(position, pnl, wallet, connector=connector):
            return 'updated'
        if cls.maybe_attach_protective_sl(position, wallet, connector=connector):
            return 'updated'
        return ''

    @classmethod
    def maybe_close_if_trailed_sl_hit(cls, position: Position, pnl: float, wallet: WalletAccount) -> bool:
        """LOCAL / fallback: lãi đã khoá bằng SL thì đóng SL_HIT khi thụt về ngưỡng."""
        if cls.wallet_trail_sl_lock_usd(wallet) <= 0:
            return False
        locked = cls.locked_profit_from_sl(
            position.symbol, position.position_type, position.open_price,
            position.lot_size, position.stop_loss,
        )
        if locked <= 0:
            return False
        try:
            net = float(pnl or 0)
        except (TypeError, ValueError):
            net = 0.0
        if net > locked + 0.009:
            return False
        ok, _ = cls.close_position(position, reason='SL_HIT', defer_sync=True)
        return bool(ok)

    @classmethod
    def compute_stop_loss_price(cls, symbol_name, direction, open_price, lot, wallet: WalletAccount):
        """Giá SL tương ứng max_stop_loss_usd / (contract * lot)."""
        max_loss = cls.wallet_max_stop_loss_usd(wallet)
        if max_loss <= 0 or open_price is None:
            return None
        sym = SymbolConfig.objects.filter(symbol=symbol_name).first()
        if not sym:
            return None
        cs = float(sym.contract_size or 100.0)
        lot_f = float(lot or 0.01)
        if cs * lot_f <= 0:
            return None
        dist = max_loss / (cs * lot_f)
        digits = int(getattr(sym, 'digits', 5) or 5)
        open_p = float(open_price)
        raw = (open_p - dist) if str(direction).upper() == 'BUY' else (open_p + dist)
        if raw <= 0:
            return None
        return Decimal(str(round(raw, digits)))

    @classmethod
    def price_hit_stop_loss(cls, position: Position, curr_p: float) -> bool:
        sl = position.stop_loss
        if sl is None:
            return False
        try:
            slf = float(sl)
        except (TypeError, ValueError):
            return False
        if slf <= 0:
            return False
        if position.position_type == 'BUY':
            return curr_p <= slf
        return curr_p >= slf

    @classmethod
    def can_wallet_open_or_pyramid(cls, wallet: WalletAccount, sym_name: str, forecast_dir: str) -> tuple[bool, bool, str]:
        """
        Cho phép đánh nhiều lệnh cùng lúc đến hạn mức max_open_trades của ví.
        Không chặn theo % rủi ro lệnh hay lỗ tối đa ngày.
        """
        live_acc = None
        try:
            from apps.trading.mt5_session import MT5NativeSession
            if wallet.mt5_login and MT5NativeSession.available():
                acc = MT5NativeSession.account()
                if acc and str(acc.login) == str(wallet.mt5_login):
                    live_acc = acc
        except Exception:
            live_acc = None

        if live_acc is not None:
            margin_level = float(getattr(live_acc, 'margin_level', 0) or 0.0)
            margin_free = float(getattr(live_acc, 'margin_free', 0) or 0.0)
            live_margin = float(getattr(live_acc, 'margin', 0) or 0.0)
            live_bal = float(getattr(live_acc, 'balance', 0) or 0.0)
            live_eq = float(getattr(live_acc, 'equity', 0) or 0.0)
        else:
            margin_level = float(wallet.margin_level or 0.0)
            margin_free = float(wallet.margin_free or 0.0)
            live_margin = float(wallet.margin or 0.0)
            live_bal = float(wallet.balance or wallet.capital or 0)
            live_eq = float(wallet.equity or 0)

        if not AutoPlanGenerator.wallet_has_capital(wallet, live_equity=live_eq, live_balance=live_bal):
            AutoPlanGenerator.purge_all_plans_for_wallet(wallet)
            return False, False, "Ví về 0 (đã thanh lý / hết tiền). Không lập kế hoạch, không đặt lệnh."

        # Chỉ chặn khi sát ngưỡng stop-out, không giới hạn 300% như trước
        if margin_level > 0 and margin_level < 80.0:
            return False, False, f"Mức ký quỹ quá thấp ({margin_level:.1f}%). Tạm dừng mở thêm lệnh."

        if live_margin > 0 and margin_free < 1.0:
            return False, False, f"Free Margin ${margin_free:.2f} không đủ để mở thêm lệnh."

        all_open_positions = wallet.positions.all()
        total_open_count = cls.count_open_positions(wallet)
        max_allowed = cls.get_max_allowed_positions_for_wallet(wallet)

        if total_open_count >= max_allowed:
            AutoPlanGenerator.purge_pending_plans(wallet)
            return False, False, f"Đã đạt số lệnh đồng thời tối đa của ví ({total_open_count}/{max_allowed})."

        if not forecast_dir:
            return False, False, "Chưa có hướng lướt sóng (BUY/SELL) rõ ràng."

        sym_positions = all_open_positions.filter(symbol=sym_name)
        if not sym_positions.exists():
            return True, False, f"Mở lệnh lướt sóng {forecast_dir} {sym_name} ({total_open_count + 1}/{max_allowed})"

        first_pos = sym_positions.first()
        if forecast_dir != first_pos.position_type:
            return False, False, f"Đang có lệnh {first_pos.position_type} {sym_name}. Không đảo chiều; chờ chốt lời hoặc cắt lỗ."

        return True, True, f"Mở thêm lệnh lướt sóng {forecast_dir} {sym_name} ({total_open_count + 1}/{max_allowed})"

    @classmethod
    def wallet_can_autotrade(cls, wallet: WalletAccount | None) -> bool:
        """Chỉ vào lệnh mới khi ví active + user đã Bật Bot + (sim hoặc MT5 đã login đúng ví)."""
        if not wallet or not getattr(wallet, 'is_active', False):
            return False
        if str(getattr(wallet, 'bot_status', '') or '') != 'RUNNING':
            return False
        login = str(getattr(wallet, 'mt5_login', '') or '').replace('#', '').strip()
        if not login:
            return True
        return ExnessMT5Connector.session_login_matches(wallet)

    @classmethod
    def try_immediate_market_entries(cls, forecasts: dict | None = None):
        """
        Chỉ lập plan khi còn slot lệnh (open < max). Lặp khớp MARKET đến khi đủ max_open_trades.
        Đủ lệnh / không vào được → xóa plan chờ, không treo hàng đợi.
        """
        active_wallets = [
            w for w in WalletAccount.objects.filter(is_active=True, bot_status='RUNNING')
            if cls.wallet_can_autotrade(w)
        ]
        packed = dict(forecasts or {})
        if not packed:
            needed = set()
            for w in active_wallets:
                needed.update(w.allowed_symbols)
            for sym_name in needed:
                fc = MarketForecast.objects.filter(symbol=sym_name).order_by('-updated_at').first()
                sym = SymbolConfig.objects.filter(symbol=sym_name).first()
                if fc and sym:
                    packed[sym_name] = (sym, fc)

        for wallet in active_wallets:
            if not AutoPlanGenerator.wallet_has_capital(wallet):
                AutoPlanGenerator.purge_all_plans_for_wallet(wallet)
                continue

            if wallet.mt5_login:
                try:
                    connector = ExnessMT5Connector(
                        login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server
                    )
                    if connector.connect(allow_switch=True):
                        connector.sync_positions(wallet)
                except Exception:
                    pass

            if cls.count_open_positions(wallet) >= cls.get_max_allowed_positions_for_wallet(wallet):
                AutoPlanGenerator.purge_pending_plans(wallet)
                continue

            max_n = cls.get_max_allowed_positions_for_wallet(wallet)
            fail_streak = 0
            attempts_left = max_n
            while cls.count_open_positions(wallet) < max_n and fail_streak < 5 and attempts_left > 0:
                opened_any = False
                blocked_all = True
                for sym_name in wallet.allowed_symbols:
                    if cls.count_open_positions(wallet) >= max_n:
                        AutoPlanGenerator.purge_pending_plans(wallet)
                        break
                    item = packed.get(sym_name)
                    if not item:
                        AutoPlanGenerator.purge_pending_plans(wallet, symbol=sym_name)
                        continue
                    sym_config, forecast = item
                    forecast_dir = AutoPlanGenerator.direction_from_forecast(forecast)
                    if not forecast_dir:
                        # MONITORING / WAIT_FOR_PULLBACK — chưa đủ tín hiệu nến, không vào lệnh
                        AutoPlanGenerator.purge_pending_plans(wallet, symbol=sym_name)
                        continue
                    can_enter, is_pyramiding, _reason = cls.can_wallet_open_or_pyramid(
                        wallet, sym_name, forecast_dir
                    )
                    if not can_enter:
                        AutoPlanGenerator.purge_pending_plans(wallet, symbol=sym_name)
                        continue
                    blocked_all = False
                    plan = None
                    try:
                        plan = AutoPlanGenerator.generate_plan_for_wallet(
                            wallet, sym_config, forecast, is_pyramiding=is_pyramiding
                        )
                        if not plan or plan.status not in ('PENDING_TRIGGER', 'PENDING'):
                            continue
                        attempts_left -= 1
                        pos = cls.trigger_plan_to_position(plan)
                        if pos is not None:
                            opened_any = True
                            fail_streak = 0
                            if cls.count_open_positions(wallet) >= max_n:
                                AutoPlanGenerator.purge_pending_plans(wallet)
                                break
                            if wallet.mt5_login:
                                time.sleep(0.12)
                        else:
                            AutoPlanGenerator.discard_plan(plan)
                            fail_streak += 1
                            if wallet.mt5_login:
                                time.sleep(0.25)
                            if fail_streak >= 5:
                                break
                    except Exception as pe:
                        AutoPlanGenerator.discard_plan(plan)
                        fail_streak += 1
                        BotLog.log(
                            level='ERROR',
                            category='PLAN',
                            message=f"Lỗi vào lệnh MARKET cho ví '{wallet.name}' ({sym_name}): {pe}",
                            traceback=traceback.format_exc(),
                            wallet=wallet,
                            symbol=sym_name
                        )
                if blocked_all:
                    break
                if not opened_any:
                    break
            AutoPlanGenerator.purge_pending_plans(wallet)

    @classmethod
    def refill_plans_after_close(cls, wallet: WalletAccount | None):
        """Sau khi đóng lệnh: nếu bot RUNNING và còn slot thì lập plan mới theo phân tích hiện tại rồi khớp ngay."""
        if not wallet or not cls.wallet_can_autotrade(wallet):
            if wallet:
                AutoPlanGenerator.purge_pending_plans(wallet)
            return
        if cls.count_open_positions(wallet) >= cls.get_max_allowed_positions_for_wallet(wallet):
            AutoPlanGenerator.purge_pending_plans(wallet)
            return
        try:
            cls.try_immediate_market_entries()
        except Exception:
            logger.warning("refill_plans_after_close: %s", traceback.format_exc())

    @classmethod
    def persist_mt5_state(cls):
        """Ghi account + positions từ native MT5 (Windows) hoặc Wine Bridge (Linux)."""
        from apps.trading.mt5_session import MT5NativeSession
        if MT5NativeSession.available() and MT5NativeSession.ensure():
            acc = MT5NativeSession.account()
            if acc is None:
                return
            login = str(acc.login)
            wallet = WalletAccount.objects.filter(mt5_login=login, is_active=True).first()
            if not wallet:
                return
            wallet.balance = Decimal(str(round(acc.balance, 2)))
            wallet.equity = Decimal(str(round(acc.equity, 2)))
            wallet.floating_pnl = Decimal(str(round(acc.profit, 2)))
            wallet.margin = Decimal(str(round(acc.margin, 2)))
            wallet.margin_free = Decimal(str(round(acc.margin_free, 2)))
            wallet.margin_level = float(acc.margin_level) if acc.margin_level else 0.0
            wallet.today_pnl = wallet.get_today_pnl()
            wallet.save(update_fields=[
                'balance_db', 'equity_db', 'floating_pnl', 'margin',
                'margin_free', 'margin_level', 'today_pnl',
            ])
            if not AutoPlanGenerator.wallet_has_capital(wallet, live_equity=float(acc.equity or 0), live_balance=float(acc.balance or 0)):
                AutoPlanGenerator.purge_all_plans_for_wallet(wallet)
            connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
            connector.is_connected = True
            connector.sync_positions(wallet)
            return

        wallet = WalletAccount.get_current()
        if not wallet or not wallet.mt5_login:
            return
        try:
            connector = ExnessMT5Connector(
                login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server
            )
            if connector.connect(allow_switch=True):
                connector.sync_account_info(wallet)
                connector.sync_positions(wallet)
                wallet.refresh_from_db()
                if not AutoPlanGenerator.wallet_has_capital(wallet):
                    AutoPlanGenerator.purge_all_plans_for_wallet(wallet)
        except Exception:
            return

    @classmethod
    def _tag_bot_batch_closes(cls, tickets: list, reason: str) -> None:
        if not tickets:
            return
        from apps.trading.models import OrderSourceTag
        tags = [
            OrderSourceTag(ticket=t, source='BOT', magic=8882026, close_reason=reason)
            for t in tickets
        ]
        try:
            OrderSourceTag.objects.bulk_create(
                tags,
                update_conflicts=True,
                unique_fields=['ticket'],
                update_fields=['source', 'magic', 'close_reason'],
            )
        except Exception:
            pass

    @classmethod
    def update_positions_and_pnl(cls, sync_mt5: bool = False):
        """
        PnL lấy profit/price_current từ MT5 khi terminal sống.
        Tự chốt TP khi lãi >= min_take_profit_usd; cắt SL khi lỗ <= -max_stop_loss_usd.
        Sau khi đóng, nếu còn slot thì lập plan và khớp lệnh bù.
        """
        try:
            from apps.trading.mt5_session import MT5NativeSession
            now = time.time()
            if sync_mt5 or (now - cls._last_persist) >= 1.0:
                cls._last_persist = now
                if sync_mt5:
                    for wallet in WalletAccount.objects.filter(is_active=True, mt5_login__isnull=False):
                        try:
                            connector = ExnessMT5Connector(
                                login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server
                            )
                            if connector.connect(allow_switch=True):
                                connector.sync_account_info(wallet)
                                connector.sync_positions(wallet)
                        except Exception:
                            pass
                else:
                    cls.persist_mt5_state()

            live_rows = []
            try:
                live_rows = MT5NativeSession.positions() or []
            except Exception:
                live_rows = []
            live_by_ticket = {str(p.ticket): p for p in live_rows} if live_rows else {}
            live_account = MT5NativeSession.account() if MT5NativeSession.available() else None
            live_login = str(getattr(live_account, 'login', '') or '') if live_account else ''

            wallets_by_login = {
                str(w.mt5_login): w
                for w in WalletAccount.objects.filter(is_active=True, mt5_login__isnull=False).exclude(mt5_login='')
            }
            sl_conn_cache: dict = {}
            symbol_map = {s.symbol: s for s in SymbolConfig.objects.all()}
            positions_to_save = []
            closed_any = False
            live_count = len(live_rows)

            # --- 1) Chốt/cắt nhanh hàng loạt theo MT5 live (không tạo Position từng ticket) ---
            if live_rows and live_login:
                wallet_live = wallets_by_login.get(live_login) or WalletAccount.get_current()
                if wallet_live and str(wallet_live.mt5_login or '') == live_login:
                    min_tp = cls.wallet_min_take_profit_usd(wallet_live)
                    max_sl = cls.wallet_max_stop_loss_usd(wallet_live)
                    batch_n = 200 if live_count >= 50 else 50
                    if min_tp > 0:
                        batch = MT5NativeSession.close_positions_batch(
                            live_rows,
                            comment='BotClose',
                            only_profit_ge=min_tp,
                            max_n=batch_n,
                        )
                        closed_tickets = batch.get('closed') or []
                        if closed_tickets:
                            closed_any = True
                            cls._tag_bot_batch_closes(closed_tickets, 'TP_HIT')
                            # Xóa plan EXECUTING gắn vị thế vừa đóng (tránh tích đống kế hoạch mồ côi)
                            plan_ids = list(
                                Position.objects.filter(ticket__in=closed_tickets)
                                .exclude(plan_id=None)
                                .values_list('plan_id', flat=True)
                            )
                            Position.objects.filter(ticket__in=closed_tickets).delete()
                            if plan_ids:
                                TradingPlan.objects.filter(pk__in=plan_ids).delete()
                            if batch.get('error'):
                                logger.warning("Batch tự chốt dừng sớm: %s (đã đóng %s)", batch['error'], len(closed_tickets))
                            try:
                                live_rows = MT5NativeSession.positions() or []
                                live_by_ticket = {str(p.ticket): p for p in live_rows}
                                live_count = len(live_rows)
                            except Exception:
                                pass
                    if max_sl > 0 and live_rows:
                        batch_sl = MT5NativeSession.close_positions_batch(
                            live_rows,
                            comment='BotSL',
                            only_profit_le=-max_sl,
                            max_n=batch_n,
                        )
                        sl_tickets = batch_sl.get('closed') or []
                        if sl_tickets:
                            closed_any = True
                            cls._tag_bot_batch_closes(sl_tickets, 'SL_HIT')
                            plan_ids = list(
                                Position.objects.filter(ticket__in=sl_tickets)
                                .exclude(plan_id=None)
                                .values_list('plan_id', flat=True)
                            )
                            Position.objects.filter(ticket__in=sl_tickets).delete()
                            if plan_ids:
                                TradingPlan.objects.filter(pk__in=plan_ids).delete()
                            if batch_sl.get('error'):
                                logger.warning("Batch cắt lỗ dừng sớm: %s (đã đóng %s)", batch_sl['error'], len(sl_tickets))
                            try:
                                live_rows = MT5NativeSession.positions() or []
                                live_by_ticket = {str(p.ticket): p for p in live_rows}
                                live_count = len(live_rows)
                            except Exception:
                                pass

            # --- 2) Cập nhật PnL DB (bỏ qua khi quá nhiều lệnh để không chậm) ---
            db_qs = Position.objects.select_related('wallet').filter(wallet__is_active=True)
            if live_count > 80:
                # Chỉ dọn LOCAL + ghost; không bulk_update hàng trăm dòng mỗi tick
                for position in db_qs:
                    ticket = str(position.ticket)
                    is_local = ticket.startswith('LOCAL-')
                    if not is_local and live_login and str(position.wallet.mt5_login or '') == live_login:
                        if ticket not in live_by_ticket:
                            try:
                                position.delete()
                            except Exception:
                                pass
                        continue
                    if is_local:
                        wallet = position.wallet
                        sym = symbol_map.get(position.symbol)
                        if not sym:
                            continue
                        curr_price = sym.current_price or Decimal('0')
                        if curr_price <= 0:
                            continue
                        open_p = float(position.open_price or 0)
                        curr_p = float(curr_price)
                        lot = float(position.lot_size or 0.01)
                        cs = float(sym.contract_size or 100)
                        diff = (curr_p - open_p) if position.position_type == 'BUY' else (open_p - curr_p)
                        pnl = round(diff * cs * lot, 2)
                        position.current_price = curr_price
                        reason = cls.auto_close_reason_for_pnl(wallet, pnl)
                        if reason:
                            cls.close_position(position, reason=reason, defer_sync=True)
                            closed_any = True
                        else:
                            act = cls.maybe_manage_position_sl(position, pnl, wallet)
                            if act == 'closed':
                                closed_any = True
                        continue
                    pnl_db = float(position.floating_pnl or 0)
                    reason = cls.auto_close_reason_for_pnl(position.wallet, pnl_db)
                    if reason:
                        cls.close_position(position, reason=reason, defer_sync=True)
                        closed_any = True
                    else:
                        act = cls.maybe_manage_position_sl(
                            position, pnl_db, position.wallet,
                            connector=cls.mt5_connector_for_wallet(position.wallet, sl_conn_cache),
                        )
                        if act == 'closed':
                            closed_any = True
                if closed_any:
                    w = wallets_by_login.get(live_login) or WalletAccount.get_current()
                    if w and w.mt5_login:
                        try:
                            connector = ExnessMT5Connector(login=w.mt5_login, password=w.mt5_password, server=w.mt5_server)
                            if connector.connect(allow_switch=True):
                                connector.sync_account_info(w)
                                connector.sync_positions(w)
                        except Exception:
                            pass
                    if w:
                        cls.refill_plans_after_close(w)
                return

            for position in db_qs:
                wallet = position.wallet
                live_p = live_by_ticket.get(str(position.ticket))
                sym = symbol_map.get(position.symbol)
                is_local = str(position.ticket).startswith('LOCAL-')

                if live_p:
                    curr_price = Decimal(str(live_p.price_current or 0))
                    if curr_price <= 0:
                        curr_price = Decimal(str(live_p.price_open or position.open_price or 0))
                    position.current_price = curr_price
                    open_p = float(position.open_price or live_p.price_open or 0)
                    curr_p = float(curr_price)
                    pnl = round(float(live_p.profit or 0), 2)
                    swap_fee = float(getattr(live_p, 'swap', 0) or 0)
                    position.floating_pnl = Decimal(str(pnl))
                    position.swap = Decimal(str(round(swap_fee, 2)))
                    if live_p.sl:
                        position.stop_loss = Decimal(str(live_p.sl))
                    if live_p.tp:
                        position.take_profit = Decimal(str(live_p.tp))
                else:
                    if not is_local and live_account is not None:
                        if live_login and str(wallet.mt5_login or '') == live_login:
                            try:
                                position.delete()
                            except Exception:
                                pass
                        continue
                    if not is_local:
                        # Linux/Wine: profit đã sync từ Bridge — không tính lại từ SymbolConfig
                        reason = cls.auto_close_reason_for_pnl(wallet, float(position.floating_pnl or 0))
                        if reason:
                            ok, _ = cls.close_position(position, reason=reason, defer_sync=True)
                            if ok:
                                closed_any = True
                        else:
                            act = cls.maybe_manage_position_sl(
                                position, float(position.floating_pnl or 0), wallet,
                                connector=cls.mt5_connector_for_wallet(wallet, sl_conn_cache),
                            )
                            if act == 'closed':
                                closed_any = True
                            elif act == 'updated':
                                try:
                                    position.save(update_fields=['stop_loss', 'is_trailing'])
                                except Exception:
                                    pass
                        continue
                    if not sym:
                        continue
                    curr_price = sym.current_price or Decimal("0.0")
                    if curr_price <= 0:
                        continue
                    position.current_price = curr_price
                    open_p = float(position.open_price or 0.0)
                    curr_p = float(curr_price)
                    lot = float(position.lot_size or 0.01)
                    contract_size = float(sym.contract_size or 100.0)
                    diff = (curr_p - open_p) if position.position_type == 'BUY' else (open_p - curr_p)
                    pnl = round(diff * contract_size * lot, 2)
                    actual_comm = float(position.commission or 0.0)
                    est_comm = abs(actual_comm) if actual_comm != 0.0 else round(lot * 3.0, 2)
                    swap_fee = abs(float(position.swap or 0.0))
                    net_pnl = round(pnl - est_comm - swap_fee, 2)
                    position.floating_pnl = Decimal(str(pnl))

                point_size = float(getattr(sym, 'point_size', None) or 0.0001) or 0.0001
                cat = getattr(sym, 'category', 'FOREX') if sym else 'FOREX'
                digits = getattr(sym, 'digits', 2) if sym else 2
                if digits in [3, 5]:
                    pip_size = point_size * 10.0
                elif 'XAU' in position.symbol or cat == 'METALS':
                    pip_size = 0.10
                elif 'BTC' in position.symbol or 'ETH' in position.symbol or cat == 'CRYPTO':
                    pip_size = 1.00
                else:
                    pip_size = max(point_size * 10.0, 0.0001)
                diff = (curr_p - open_p) if position.position_type == 'BUY' else (open_p - curr_p)
                position.floating_pips = round(diff / pip_size, 1) if pip_size else 0.0

                if position.position_type == 'BUY':
                    if position.highest_price is None or curr_price > position.highest_price:
                        position.highest_price = curr_price
                else:
                    if position.lowest_price is None or curr_price < position.lowest_price:
                        position.lowest_price = curr_price

                tp_pnl = float(getattr(live_p, 'profit', 0) or 0) if live_p else float(position.floating_pnl or 0)
                reason = cls.auto_close_reason_for_pnl(wallet, tp_pnl)
                if reason:
                    ok, _ = cls.close_position(position, reason=reason, defer_sync=True)
                    if ok:
                        closed_any = True
                    continue
                act = cls.maybe_manage_position_sl(
                    position, tp_pnl, wallet,
                    connector=cls.mt5_connector_for_wallet(wallet, sl_conn_cache),
                )
                if act == 'closed':
                    closed_any = True
                    continue
                positions_to_save.append(position)

            if positions_to_save:
                Position.objects.bulk_update(positions_to_save, [
                    'current_price', 'floating_pnl', 'floating_pips',
                    'highest_price', 'lowest_price', 'stop_loss', 'take_profit', 'swap',
                    'is_trailing',
                ])

            if closed_any:
                w = wallets_by_login.get(live_login) or WalletAccount.get_current()
                if w and w.mt5_login:
                    try:
                        connector = ExnessMT5Connector(login=w.mt5_login, password=w.mt5_password, server=w.mt5_server)
                        if connector.connect(allow_switch=True):
                            connector.sync_account_info(w)
                            connector.sync_positions(w)
                            # History nặng — chỉ sync khi số lệnh còn lại vừa phải
                            if live_count <= 80:
                                connector.sync_history_from_mt5(w)
                    except Exception as se:
                        logger.warning("Sync sau tự chốt: %s", se)
                if w:
                    cls.refill_plans_after_close(w)

            if live_account is None:
                cls.recalculate_all_wallets()
        except Exception as e:
            BotLog.log(level='ERROR', category='EXECUTION', message=f'Lỗi khi cập nhật vị thế: {e}', traceback=traceback.format_exc())

    @classmethod
    def close_position(cls, position: Position, close_price: Decimal = None, reason: str = 'TP_HIT', *, defer_sync: bool = False) -> tuple[bool, str]:
        """Đóng vị thế THẬT trên sàn Exness MT5 và lưu vào lịch sử (Tính toán Net PnL sau phí)."""
        wallet = position.wallet
        ticket_str = str(position.ticket)
        is_local_sim = ticket_str.startswith('LOCAL-')
        connector = None
        res_close = None
        msg = f'Đã đóng lệnh #{ticket_str}'

        now = time.time()
        if ticket_str in cls._closing:
            return False, f'Đang đóng lệnh #{ticket_str}.'
        until = cls._close_cooldown.get(ticket_str, 0)
        if reason != 'MANUAL_CLOSE' and now < until:
            return False, f'Chờ gửi lại đóng lệnh #{ticket_str}.'

        if not is_local_sim and wallet and wallet.mt5_login:
            cls._closing.add(ticket_str)
            try:
                connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
                if not connector.connect(allow_switch=bool(getattr(wallet, 'is_active', False))):
                    return False, "Chưa kết nối MT5 Terminal để đóng lệnh. Hãy mở MT5, đăng nhập và bật Algo Trading."
                close_comment = 'WebManual' if reason == 'MANUAL_CLOSE' else 'BotClose'
                close_magic = 0 if reason == 'MANUAL_CLOSE' else 8882026
                res_close = connector.close_order(
                    ticket=ticket_str,
                    symbol=position.symbol,
                    order_type=position.position_type,
                    volume=float(position.lot_size),
                    comment=close_comment,
                    magic=close_magic,
                )
            finally:
                cls._closing.discard(ticket_str)
            if res_close is None:
                return False, "Chưa kết nối MT5 Terminal/Bridge để đóng lệnh."
            if isinstance(res_close, tuple):
                ok = res_close[0]
                msg = res_close[1]
                res_data = res_close[2] if len(res_close) > 2 else {}
            else:
                ok = bool(res_close)
                msg = f"Đã đóng thành công lệnh #{ticket_str}" if ok else "Lỗi đóng lệnh trên MT5"
                res_data = {}

            if not ok:
                retryable = bool(res_data.get('retryable')) or any(
                    k in str(msg).lower() for k in ('requote', '10004', '10020', '10021', '10012', 'off quotes', 'no prices')
                )
                wait_s = 8 if 'đang đóng' in str(msg).lower() or 'market closed' in str(msg).lower() else 3
                cls._close_cooldown[ticket_str] = now + wait_s
                if retryable:
                    logger.warning("Đóng lệnh #%s tạm thất bại (sẽ thử lại): %s", ticket_str, msg)
                    return False, msg
                BotLog.log(
                    level='ERROR',
                    category='EXECUTION',
                    message=f"Sàn Exness MT5 từ chối đóng lệnh #{position.ticket}: {msg}",
                    wallet=wallet,
                    symbol=position.symbol
                )
                return False, f"Sàn Exness MT5 từ chối đóng lệnh: {msg}"
            cls._close_cooldown.pop(ticket_str, None)

        from apps.trading.order_source import remember_order_source, remember_close_reason
        # Đóng tay / đóng toàn bộ → nguồn USER; bot tự chốt giữ nguồn mở lệnh gốc
        if reason == 'MANUAL_CLOSE':
            hist_source = 'USER'
            hist_magic = 0
        else:
            hist_source = getattr(position, 'source', 'BOT') or ('BOT' if position.plan else 'USER')
            hist_magic = getattr(position, 'magic', 0) or (8882026 if hist_source == 'BOT' else 0)
        remember_order_source(ticket_str, hist_source, hist_magic)
        remember_close_reason(ticket_str, reason, source=hist_source, magic=hist_magic)

        if not is_local_sim and wallet and wallet.mt5_login and connector:
            linked_plan = position.plan
            try:
                position.delete()
            except Exception:
                pass
            if not defer_sync:
                try:
                    connector.sync_account_info(wallet)
                    connector.sync_positions(wallet)
                    connector.sync_history_from_mt5(wallet)
                    wallet.refresh_from_db()
                    wallet.calculate_metrics()
                    wallet.today_pnl = wallet.get_today_pnl()
                    wallet.save(update_fields=['today_pnl'])
                except Exception:
                    pass
            if linked_plan:
                AutoPlanGenerator.discard_plan(linked_plan)
            BotLog.log(
                level='INFO',
                category='EXECUTION',
                message=f"ĐÃ ĐÓNG LỆNH #{ticket_str} trên Exness MT5 ({reason}/{hist_source}). Lịch sử/số dư lấy từ terminal.",
                wallet=wallet,
                symbol=getattr(position, 'symbol', ''),
            )
            if not defer_sync:
                cls.refill_plans_after_close(wallet)
            return True, msg

        if close_price is not None:
            position.current_price = close_price

        # Tính toán Lợi Nhuận Ròng Thực Tế sau khi trừ Phí Hoa Hồng và Swap
        comm_val = position.commission or Decimal('0.00')
        swap_val = position.swap or Decimal('0.00')
        gross_pnl = position.floating_pnl
        net_pnl = gross_pnl + comm_val + swap_val
        is_win = net_pnl > Decimal('0.00')

        pos_comment = getattr(position, 'comment', '') or ('AutoBot' if hist_source == 'BOT' else 'Manual Trade')

        TradeHistory.objects.update_or_create(
            wallet=wallet,
            ticket=position.ticket,
            defaults={
                'symbol': position.symbol,
                'position_type': position.position_type,
                'lot_size': position.lot_size,
                'open_price': position.open_price,
                'close_price': position.current_price,
                'stop_loss': position.stop_loss,
                'take_profit': position.take_profit,
                'pnl': net_pnl,
                'commission': comm_val,
                'swap': swap_val,
                'pips': position.floating_pips,
                'close_reason': reason,
                'is_win': is_win,
                'source': hist_source,
                'magic': hist_magic,
                'comment': pos_comment,
                'opened_at': position.opened_at,
                'closed_at': timezone.now()
            }
        )

        # Cập nhật số dư ví, thống kê và winrate theo Lợi Nhuận Ròng Thực Nhận
        if wallet:
            wallet.total_profit = Decimal(str(wallet.total_profit)) + net_pnl
            wallet.balance = Decimal(str(wallet.capital)) + Decimal(str(wallet.total_profit))
            wallet.total_trades += 1
            if is_win:
                wallet.winning_trades += 1
            else:
                wallet.losing_trades += 1
            wallet.calculate_metrics()
            wallet.today_pnl = wallet.get_today_pnl()
            wallet.save()

            if not is_local_sim and wallet.mt5_login:
                try:
                    connector.sync_account_info(wallet)
                    connector.sync_history_from_mt5(wallet)
                except Exception:
                    pass

        BotLog.log(
            level='INFO',
            category='EXECUTION',
            message=f"ĐÃ ĐÓNG LỆNH #{position.ticket}: {position.position_type} {position.symbol} (Net PnL: {net_pnl:+} USD, Phí: {comm_val} USD, Lý do: {reason})",
            wallet=wallet,
            symbol=position.symbol
        )
        
        # Cập nhật trạng thái Plan nếu có
        if position.plan:
            AutoPlanGenerator.discard_plan(position.plan)

        position.delete()
        if not defer_sync:
            cls.refill_plans_after_close(wallet)
        return True, f"Đã đóng thành công lệnh #{ticket_str}"

    @classmethod
    def close_all_open(cls, wallet: WalletAccount | None = None) -> tuple[bool, str, dict]:
        """Đóng toàn bộ vị thế live MT5 bằng batch nhanh. Gắn USER + MANUAL_CLOSE. Sync nhẹ 1 lần cuối."""
        from apps.trading.mt5_session import MT5NativeSession
        from apps.trading.models import OrderSourceTag

        if wallet is None:
            wallet = WalletAccount.get_current()
        if not wallet:
            return False, 'Chưa có ví đang kích hoạt để đóng lệnh.', {'closed': 0, 'total': 0, 'errors': []}
        if not getattr(wallet, 'is_active', False):
            return False, 'Ví đang tắt kích hoạt — không thao tác MT5.', {'closed': 0, 'total': 0, 'errors': []}

        connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
        if wallet.mt5_login and not connector.connect(allow_switch=True):
            return False, 'Chưa kết nối MT5. Mở terminal, đăng nhập và bật Algo Trading.', {
                'closed': 0, 'total': 0, 'errors': ['MT5 chưa kết nối / Algo tắt'],
            }

        closed = 0
        errors: list[str] = []
        total = 0

        # LOCAL sim
        for pos in list(Position.objects.filter(wallet=wallet, ticket__startswith='LOCAL-')):
            total += 1
            ok, msg = cls.close_position(pos, reason='MANUAL_CLOSE', defer_sync=True)
            if ok:
                closed += 1
            else:
                errors.append(f'#{pos.ticket}: {msg}')

        live = []
        try:
            if wallet.mt5_login:
                live = MT5NativeSession.positions() or []
        except Exception:
            live = []

        if live:
            total += len(live)
            # Đóng theo lô 300 đến hết
            remaining = list(live)
            rounds = 0
            while remaining and rounds < 20:
                rounds += 1
                batch = MT5NativeSession.close_positions_batch(
                    remaining, comment='WebManual', only_profit_ge=None, max_n=300,
                )
                done = batch.get('closed') or []
                if done:
                    closed += len(done)
                    tags = [
                        OrderSourceTag(ticket=t, source='USER', magic=0, close_reason='MANUAL_CLOSE')
                        for t in done
                    ]
                    try:
                        OrderSourceTag.objects.bulk_create(
                            tags,
                            update_conflicts=True,
                            unique_fields=['ticket'],
                            update_fields=['source', 'magic', 'close_reason'],
                        )
                    except Exception:
                        for t in done:
                            try:
                                OrderSourceTag.objects.update_or_create(
                                    ticket=t,
                                    defaults={'source': 'USER', 'magic': 0, 'close_reason': 'MANUAL_CLOSE'},
                                )
                            except Exception:
                                pass
                    Position.objects.filter(ticket__in=done).delete()
                if batch.get('error'):
                    errors.append(batch['error'])
                    break
                failed = set(batch.get('failed') or [])
                # Lấy lại live còn lại
                try:
                    remaining = MT5NativeSession.positions() or []
                except Exception:
                    remaining = []
                if failed and not done:
                    for t in list(failed)[:5]:
                        errors.append(f'#{t}: MT5 từ chối đóng')
                    break
                if not remaining:
                    break

        # Linux / Wine Bridge: native không trả live → đóng từng lệnh DB qua connector
        leftover = list(Position.objects.filter(wallet=wallet))
        for pos in leftover:
            total += 1
            ok, msg = cls.close_position(pos, reason='MANUAL_CLOSE', defer_sync=True)
            if ok:
                closed += 1
            else:
                errors.append(f'#{pos.ticket}: {msg}')

        if wallet.mt5_login:
            try:
                connector.sync_account_info(wallet)
                connector.sync_positions(wallet)
                # History đầy đủ chỉ khi còn ít lệnh (tránh treo khi vừa đóng 1000)
                left = 0
                try:
                    left = len(MT5NativeSession.positions() or [])
                except Exception:
                    left = 0
                if left <= 30:
                    connector.sync_history_from_mt5(wallet)
                wallet.refresh_from_db()
                wallet.calculate_metrics()
                wallet.today_pnl = wallet.get_today_pnl()
                wallet.save(update_fields=['today_pnl'])
            except Exception as e:
                logger.warning('close_all sync sau đóng: %s', e)

        BotLog.log(
            level='INFO',
            category='EXECUTION',
            message=f"Đóng toàn bộ nhanh: {closed}/{total} lệnh (USER / MANUAL_CLOSE).",
            wallet=wallet,
        )
        if closed > 0:
            cls.refill_plans_after_close(wallet)
        payload = {'closed': closed, 'total': total, 'errors': errors}
        if total == 0:
            return True, 'Không có vị thế mở để đóng.', payload
        if closed >= total and not errors:
            return True, f'Đã đóng thành công toàn bộ {closed} lệnh (nguồn USER).', payload
        if closed > 0:
            return True, f'Đã đóng {closed}/{total} lệnh.', payload
        return False, (errors[0] if errors else 'Không đóng được lệnh nào. Kiểm tra Algo Trading.'), payload

    @classmethod
    def recalculate_all_wallets(cls):
        """Tính lại Equity và Floating PnL tổng cho từng ví."""
        for wallet in WalletAccount.objects.all():
            positions = wallet.positions.all()
            total_floating = sum((pos.floating_pnl for pos in positions), Decimal('0.00'))
            wallet.floating_pnl = total_floating
            wallet.equity = Decimal(str(wallet.balance)) + total_floating
            wallet.calculate_metrics()
            wallet.save()

    @classmethod
    def run_full_trading_cycle(cls):
        """
        Chạy 1 chu kỳ giao dịch hoàn chỉnh:
        Tự động đồng bộ số dư, vị thế & 100% lịch sử khớp lệnh từ Exness MT5.
        """
        try:
            AutoPlanGenerator.purge_dead_plans()

            # Step 0: Đồng bộ số dư, vị thế realtime và toàn bộ lịch sử khớp lệnh trực tiếp từ Exness MT5
            for wallet in WalletAccount.objects.filter(is_active=True):
                if wallet.mt5_login:
                    try:
                        connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
                        if connector.connect(allow_switch=True):
                            connector.sync_account_info(wallet)
                            connector.sync_positions(wallet)
                    except Exception as me:
                        logger.debug(f"MT5 sync notice #{wallet.mt5_login}: {me}")

            # Step 1: Đồng bộ giá thị trường thực tế từ MT5
            cls.sync_symbol_prices_from_mt5()

            # Step 2: Generate technical forecasts for symbols required by active wallets
            active_wallets = [
                w for w in WalletAccount.objects.filter(is_active=True, bot_status='RUNNING')
                if cls.wallet_can_autotrade(w)
            ]
            needed_symbols = set()
            for w in active_wallets:
                needed_symbols.update(w.allowed_symbols)
            
            if not needed_symbols:
                needed_symbols = set(SymbolConfig.objects.filter(is_active=True).values_list('symbol', flat=True)[:5])

            forecasts = {}
            for sym_config in SymbolConfig.objects.filter(symbol__in=needed_symbols):
                try:
                    forecast = TechnicalAnalyzer.generate_market_analysis(sym_config)
                    if forecast:
                        forecasts[sym_config.symbol] = (sym_config, forecast)
                except Exception as fe:
                    BotLog.log(
                        level='ERROR',
                        category='ANALYSIS',
                        message=f"Lỗi phân tích kỹ thuật cặp {sym_config.symbol}: {fe}",
                        traceback=traceback.format_exc(),
                        symbol=sym_config.symbol
                    )

            # Step 3: BUY/SELL MARKET ngay nếu ví chưa đủ lệnh — không chờ vùng entry
            cls.try_immediate_market_entries(forecasts)

            # Step 5: Update all positions & trailing stops
            cls.update_positions_and_pnl()

        except Exception as e:
            BotLog.log(
                level='CRITICAL',
                category='SYSTEM',
                message=f"Lỗi nghiêm trọng trong chu kỳ giao dịch của Bot: {e}",
                traceback=traceback.format_exc()
            )
