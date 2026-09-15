import logging
import time
import traceback
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
    Bộ máy thực thi: chỉ chốt lời khi lãi ròng >= min_take_profit_usd.
    Lệnh lỗ giữ nguyên (gồng) đến khi về lãi.
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
        if not wallet or not wallet.is_active:
            AutoPlanGenerator.discard_plan(plan)
            return None

        is_demo = (wallet.account_type in ['DEMO', 'SIMULATION']) or ('Trial' in wallet.mt5_server) or ('Demo' in wallet.mt5_server)
        connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
        connected_mt5 = connector.connect()

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
        if sym_live:
            mkt_price = AutoPlanGenerator.market_entry_price(sym_live, plan.direction)
            if mkt_price > 0:
                plan.entry_price = mkt_price
                plan.entry_zone_low = mkt_price
                plan.entry_zone_high = mkt_price
                plan.save(update_fields=['entry_price', 'entry_zone_low', 'entry_zone_high'])

        if connected_mt5:
            order_res = connector.send_order(
                symbol=plan.symbol,
                order_type=plan.direction,
                volume=float(plan.calculated_lot),
                price=0.0,
                sl=0.0,
                tp=0.0,
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
            connector.sync_account_info(wallet)
            connector.sync_positions(wallet)
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
                    stop_loss=None,
                    source='BOT',
                    magic=BOT_MAGIC,
                    comment=f"AI-{plan.symbol}",
                    opened_at=timezone.now(),
                )
            else:
                position.plan = plan
                position.source = 'BOT'
                position.magic = BOT_MAGIC
                position.save(update_fields=['plan', 'source', 'magic'])
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
                plan.status = 'PENDING'
                plan.save()
                BotLog.log(
                    level='WARNING',
                    category='EXECUTION',
                    wallet=wallet,
                    symbol=plan.symbol,
                    message=f"Chưa thể gửi lệnh '{plan.symbol}' vì MT5 Terminal chưa sẵn sàng kết nối. Hệ thống không tạo lệnh ảo và sẽ đồng bộ khi MT5 kết nối.",
                )
                return None
            else:
                # Chỉ khi ví hoàn toàn không cấu hình MT5 (ví mô phỏng nội bộ)
                ticket = f"LOCAL-{int(timezone.now().timestamp())}"
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
            take_profit=None,
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
    def get_max_allowed_positions_for_wallet(cls, wallet: WalletAccount) -> int:
        """Số lệnh mở đồng thời tối đa = cấu hình từng ví (max_open_trades)."""
        try:
            n = int(wallet.max_open_trades or 1)
        except (TypeError, ValueError):
            n = 1
        return max(1, min(n, 500))

    @classmethod
    def wallet_max_loss_usd(cls, wallet: WalletAccount) -> float:
        """Số USD lỗ tối đa mỗi lệnh = balance * risk_percent / 100."""
        try:
            risk = float(wallet.risk_percent or 0)
        except (TypeError, ValueError):
            risk = 0.0
        if risk <= 0:
            return 0.0
        bal = float(wallet.balance or wallet.capital or 0)
        return max(0.01, round(bal * risk / 100.0, 2))

    @classmethod
    def wallet_daily_loss_limit_usd(cls, wallet: WalletAccount) -> float:
        """Số USD lỗ tối đa trong ngày = balance * max_daily_loss_percent / 100."""
        try:
            pct = float(wallet.max_daily_loss_percent or 0)
        except (TypeError, ValueError):
            pct = 0.0
        if pct <= 0:
            return 0.0
        bal = float(wallet.balance or wallet.capital or 0)
        return round(bal * pct / 100.0, 2)

    @classmethod
    def wallet_scalp_loss_usd(cls, wallet: WalletAccount) -> float:
        """Lướt sóng: cắt lỗ cùng ngưỡng đô la với chốt lời (min_tp), không vượt risk_percent."""
        try:
            min_tp = float(getattr(wallet, 'min_take_profit_usd', 1) or 1)
        except (TypeError, ValueError):
            min_tp = 1.0
        if min_tp <= 0:
            min_tp = 0.01
        cap = cls.wallet_max_loss_usd(wallet)
        if cap <= 0:
            return round(min_tp, 2)
        return round(min(cap, min_tp), 2)

    @classmethod
    def compute_stop_loss_price(cls, symbol_name, direction, open_price, lot, wallet: WalletAccount):
        """Giá SL lướt sóng: khoảng cách = min(min_tp, risk% * balance) / (contract * lot)."""
        max_loss = cls.wallet_scalp_loss_usd(wallet)
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
        Dừng mở thêm khi đã chạm % lỗ tối đa trong ngày.
        """
        margin_level = float(wallet.margin_level or 0.0)
        margin_free = float(wallet.margin_free or 0.0)

        daily_limit = cls.wallet_daily_loss_limit_usd(wallet)
        if daily_limit > 0:
            today_pnl = float(wallet.get_today_pnl() or 0)
            if today_pnl <= -daily_limit:
                return False, False, f"Đã đạt giới hạn lỗ ngày (${today_pnl:.2f} / -${daily_limit:.2f}). Tạm dừng mở lệnh."

        # Chỉ chặn khi sát ngưỡng stop-out, không giới hạn 300% như trước
        if margin_level > 0 and margin_level < 80.0:
            return False, False, f"Mức ký quỹ quá thấp ({margin_level:.1f}%). Tạm dừng mở thêm lệnh."

        if wallet.margin and wallet.margin > 0 and margin_free < 1.0:
            return False, False, f"Free Margin ${margin_free:.2f} không đủ để mở thêm lệnh."

        all_open_positions = wallet.positions.all()
        total_open_count = all_open_positions.count()
        max_allowed = cls.get_max_allowed_positions_for_wallet(wallet)

        if total_open_count >= max_allowed:
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
    def persist_mt5_state(cls):
        """Ghi account + positions của tài khoản đang login trên terminal (1 IPC, không reconnect)."""
        from apps.trading.mt5_session import MT5NativeSession
        if not MT5NativeSession.available() or not MT5NativeSession.ensure():
            return
        acc = MT5NativeSession.account()
        if acc is None:
            return
        login = str(acc.login)
        wallet = WalletAccount.objects.filter(mt5_login=login).first()
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
        connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
        connector.is_connected = True
        connector.sync_positions(wallet)

    @classmethod
    def update_positions_and_pnl(cls, sync_mt5: bool = False):
        """
        PnL lấy profit/price_current từ MT5 khi terminal sống.
        Persist account/positions tối đa 1 lần/giây.
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
                            if connector.connect():
                                connector.sync_account_info(wallet)
                                connector.sync_positions(wallet)
                        except Exception:
                            pass
                else:
                    cls.persist_mt5_state()

            live_by_ticket = MT5NativeSession.positions_by_ticket() if MT5NativeSession.available() else {}
            live_account = MT5NativeSession.account() if MT5NativeSession.available() else None

            symbol_map = {s.symbol: s for s in SymbolConfig.objects.all()}
            positions_to_save = []
            for position in Position.objects.select_related('wallet').all():
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
                    lot = float(position.lot_size or live_p.volume or 0.01)
                    pnl = round(float(live_p.profit or 0), 2)
                    swap_fee = float(getattr(live_p, 'swap', 0) or 0)
                    comm = float(position.commission or getattr(live_p, 'commission', 0) or 0)
                    net_pnl = round(pnl + swap_fee + comm, 2)
                    position.floating_pnl = Decimal(str(pnl))
                    position.swap = Decimal(str(round(swap_fee, 2)))
                    if live_p.sl:
                        position.stop_loss = Decimal(str(live_p.sl))
                    if live_p.tp:
                        position.take_profit = Decimal(str(live_p.tp))
                else:
                    if not is_local and live_account is not None:
                        # Ticket đã biến mất trên MT5 — không gửi close (tránh spam từ chối)
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

                point_size = float(getattr(sym, 'point_size', None) or 0.0001)
                if point_size <= 0:
                    point_size = 0.0001
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
                pips = round(diff / pip_size, 1) if pip_size else 0.0
                position.floating_pips = pips

                if position.position_type == 'BUY':
                    if position.highest_price is None or curr_price > position.highest_price:
                        position.highest_price = curr_price
                else:
                    if position.lowest_price is None or curr_price < position.lowest_price:
                        position.lowest_price = curr_price

                should_close = False
                close_reason = 'TP_HIT'
                min_tp = float(getattr(wallet, 'min_take_profit_usd', 1) or 1)
                if min_tp <= 0:
                    min_tp = 0.01

                if net_pnl >= min_tp and (is_local or getattr(position, 'source', 'BOT') == 'BOT'):
                    should_close = True
                    close_reason = 'TP_HIT'
                    forecast = MarketForecast.objects.filter(symbol=position.symbol).first()

                    if position.position_type == 'BUY':
                        if position.highest_price and position.highest_price > position.open_price:
                            peak_gain = float(position.highest_price) - open_p
                            curr_gain = curr_p - open_p
                            if peak_gain > 0 and curr_gain <= peak_gain * 0.75:
                                close_reason = 'TRAILING_TP'
                        if forecast and (forecast.trend_bias == 'BEARISH' or 'SELL' in str(forecast.recommended_action)):
                            close_reason = 'TREND_REVERSAL'
                    else:
                        if position.lowest_price and position.lowest_price < position.open_price:
                            peak_gain = open_p - float(position.lowest_price)
                            curr_gain = open_p - curr_p
                            if peak_gain > 0 and curr_gain <= peak_gain * 0.75:
                                close_reason = 'TRAILING_TP'
                        if forecast and (forecast.trend_bias == 'BULLISH' or 'BUY' in str(forecast.recommended_action)):
                            close_reason = 'TREND_REVERSAL'

                if should_close:
                    cls.close_position(position, reason=close_reason)
                    continue
                positions_to_save.append(position)

            if positions_to_save:
                Position.objects.bulk_update(positions_to_save, [
                    'current_price', 'floating_pnl', 'floating_pips',
                    'highest_price', 'lowest_price', 'stop_loss', 'take_profit', 'swap'
                ])

            if live_account is None:
                cls.recalculate_all_wallets()
        except Exception as e:
            BotLog.log(level='ERROR', category='EXECUTION', message=f'Lỗi khi cập nhật vị thế: {e}', traceback=traceback.format_exc())

    @classmethod
    def close_position(cls, position: Position, close_price: Decimal = None, reason: str = 'TP_HIT') -> tuple[bool, str]:
        """Đóng vị thế THẬT trên sàn Exness MT5 và lưu vào lịch sử (Tính toán Net PnL sau phí)."""
        wallet = position.wallet
        ticket_str = str(position.ticket)
        is_local_sim = ticket_str.startswith('LOCAL-')
        connector = None
        res_close = None

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
                if not connector.connect():
                    return False, "Chưa kết nối MT5 Terminal để đóng lệnh. Hãy mở MT5, đăng nhập và bật Algo Trading."
                res_close = connector.close_order(
                    ticket=ticket_str,
                    symbol=position.symbol,
                    order_type=position.position_type,
                    volume=float(position.lot_size)
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

        from apps.trading.order_source import remember_order_source
        remember_order_source(ticket_str, getattr(position, 'source', 'USER') or 'USER', getattr(position, 'magic', 0) or 0)

        if not is_local_sim and wallet and wallet.mt5_login and connector:
            linked_plan = position.plan
            try:
                position.delete()
            except Exception:
                pass
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
                message=f"ĐÃ ĐÓNG LỆNH #{ticket_str} trên Exness MT5. Lịch sử/số dư lấy từ terminal.",
                wallet=wallet,
                symbol=getattr(position, 'symbol', ''),
            )
            return True, msg

        if close_price is not None:
            position.current_price = close_price

        # Tính toán Lợi Nhuận Ròng Thực Tế sau khi trừ Phí Hoa Hồng và Swap
        comm_val = position.commission or Decimal('0.00')
        swap_val = position.swap or Decimal('0.00')
        gross_pnl = position.floating_pnl
        net_pnl = gross_pnl + comm_val + swap_val
        is_win = net_pnl > Decimal('0.00')

        pos_source = getattr(position, 'source', 'BOT') or ('BOT' if position.plan else 'USER')
        pos_magic = getattr(position, 'magic', 0) or (8882026 if pos_source == 'BOT' else 0)
        pos_comment = getattr(position, 'comment', '') or ('AutoBot' if pos_source == 'BOT' else 'Manual Trade')

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
                'source': pos_source,
                'magic': pos_magic,
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
        return True, f"Đã đóng thành công lệnh #{ticket_str}"

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
                        if connector.connect():
                            connector.sync_account_info(wallet)
                            connector.sync_positions(wallet)
                    except Exception as me:
                        logger.debug(f"MT5 sync notice #{wallet.mt5_login}: {me}")

            # Step 1: Đồng bộ giá thị trường thực tế từ MT5
            cls.sync_symbol_prices_from_mt5()

            # Step 2: Generate technical forecasts for symbols required by active wallets
            active_wallets = list(WalletAccount.objects.filter(is_active=True, bot_status='RUNNING'))
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

            # Step 3 & 4: Tự Động Vào Lệnh & Nhồi Lệnh Theo Trend Có Kiểm Soát Vốn Chống Cháy Ví
            for wallet in active_wallets:
                for sym_name in wallet.allowed_symbols:
                    if sym_name in forecasts:
                        sym_config, forecast = forecasts[sym_name]
                        forecast_dir = 'BUY' if (forecast.trend_bias == 'BULLISH' or 'BUY' in str(forecast.recommended_action)) else ('SELL' if (forecast.trend_bias == 'BEARISH' or 'SELL' in str(forecast.recommended_action)) else None)

                        can_enter, is_pyramiding, reason = cls.can_wallet_open_or_pyramid(wallet, sym_name, forecast_dir)

                        if can_enter:
                            # CẬP NHẬT KẾ HOẠCH & VÀO LỆNH THEO TREND:
                            try:
                                plan = AutoPlanGenerator.update_or_create_plan_for_wallet(
                                    wallet, sym_config, forecast, is_pyramiding=is_pyramiding
                                )
                                if plan and plan.status == 'PENDING_TRIGGER':
                                    cls.trigger_plan_to_position(plan)
                            except Exception as pe:
                                BotLog.log(
                                    level='ERROR',
                                    category='PLAN',
                                    message=f"Lỗi cập nhật kế hoạch giao dịch cho ví '{wallet.name}' ({sym_name}): {pe}",
                                    traceback=traceback.format_exc(),
                                    wallet=wallet,
                                    symbol=sym_name
                                )
                        else:
                            # Không đủ điều kiện an toàn vốn hoặc đã đạt ngưỡng lệnh -> Giữ Plan ở trạng thái chờ kích hoạt
                            try:
                                AutoPlanGenerator.update_or_create_plan_for_wallet(
                                    wallet, sym_config, forecast, is_pyramiding=False
                                )
                            except Exception:
                                pass

            # Step 5: Update all positions & trailing stops
            cls.update_positions_and_pnl()

        except Exception as e:
            BotLog.log(
                level='CRITICAL',
                category='SYSTEM',
                message=f"Lỗi nghiêm trọng trong chu kỳ giao dịch của Bot: {e}",
                traceback=traceback.format_exc()
            )
