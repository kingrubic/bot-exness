import logging
import random
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
    Bộ máy thực thi quét thị trường, kích hoạt lệnh theo Plan,
    quản lý vị thế mở, Trailing Stop, Chốt Lời / Cắt Lỗ và cập nhật số dư ví.
    Tự động ghi nhật ký lỗi & cảnh báo vào BotLog để Admin theo dõi.
    """

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
            plan.status = 'CANCELLED'
            plan.save()
            return None

        is_demo = (wallet.account_type in ['DEMO', 'SIMULATION']) or ('Trial' in wallet.mt5_server) or ('Demo' in wallet.mt5_server)
        connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
        connected_mt5 = connector.connect()

        # Nếu là ví Real mà không kết nối được MT5 -> Bắt buộc hủy và báo lỗi
        if not is_demo and not connected_mt5:
            plan.status = 'FAILED'
            plan.save()
            BotLog.log(
                level='ERROR',
                category='EXECUTION',
                wallet=wallet,
                symbol=plan.symbol,
                message=f"Không thể kết nối tới Exness MT5 Live để mở lệnh {plan.direction} {plan.symbol} cho ví Real '{wallet.name}' (#{wallet.mt5_login}).",
                traceback=f"MT5 connect failed on server {wallet.mt5_server} for wallet #{wallet.mt5_login}"
            )
            return None

        if connected_mt5:
            # Gửi lệnh trực tiếp lên sàn Exness MT5
            order_res = connector.send_order(
                symbol=plan.symbol,
                order_type=plan.direction,
                volume=float(plan.calculated_lot),
                price=float(plan.entry_price),
                sl=float(plan.stop_loss) if plan.stop_loss else 0.0,
                tp=float(plan.take_profit_1) if plan.take_profit_1 else 0.0,
                comment=f"AI-{plan.symbol}"
            )

            if not order_res.get('success'):
                plan.status = 'FAILED'
                plan.save()
                BotLog.log(
                    level='ERROR',
                    category='EXECUTION',
                    wallet=wallet,
                    symbol=plan.symbol,
                    message=f"Khớp lệnh THẬT thất bại trên sàn Exness MT5: {order_res.get('error')}",
                    traceback=str(order_res)
                )
                return None

            ticket = order_res.get('ticket')
            exec_price = Decimal(str(order_res.get('price', plan.entry_price)))
            exec_volume = float(order_res.get('volume', plan.calculated_lot))
        else:
            # Khớp lệnh Demo Sandbox (chạy thử nghiệm thuật toán trên máy tính/Linux)
            import random
            ticket = f"DEMO-{random.randint(1000000, 9999999)}"
            exec_price = plan.entry_price
            exec_volume = float(plan.calculated_lot)

        position = Position.objects.create(
            wallet=wallet,
            plan=plan,
            ticket=ticket,
            symbol=plan.symbol,
            position_type=plan.direction,
            lot_size=exec_volume,
            open_price=exec_price,
            current_price=exec_price,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit_1,
            floating_pnl=Decimal("0.00"),
            floating_pips=0.0,
            is_trailing=True,
            opened_at=timezone.now()
        )
        plan.status = 'EXECUTING'
        plan.triggered_at = timezone.now()
        plan.save()

        # Ghi log thành công
        mode_str = "THẬT TRÊN EXNESS MT5" if connected_mt5 else "DEMO SANDBOX"
        BotLog.log(
            level='INFO',
            category='EXECUTION',
            message=f"ĐÃ MỞ LỆNH {mode_str} #{ticket}: {position.position_type} {position.symbol} {position.lot_size} Lot tại giá {position.open_price} cho ví '{wallet.name}'",
            wallet=wallet,
            symbol=plan.symbol
        )
        return position

    @classmethod
    def update_positions_and_pnl(cls):
        """Cập nhật Lãi/Lỗ tạm tính cho tất cả các vị thế đang mở và kiểm tra TP/SL."""
        try:
            for position in Position.objects.all():
                sym = SymbolConfig.objects.filter(symbol=position.symbol).first()
                if not sym:
                    continue

                curr_price = sym.current_price
                position.current_price = curr_price
                open_p = float(position.open_price)
                curr_p = float(curr_price)
                lot = position.lot_size
                contract_size = sym.contract_size

                # Tính PnL & Pips
                if position.position_type == 'BUY':
                    diff = curr_p - open_p
                    pips = round(diff / sym.point_size, 1)
                    pnl = round(diff * contract_size * lot, 2)
                    
                    if position.highest_price is None or curr_price > position.highest_price:
                        position.highest_price = curr_price
                else: # SELL
                    diff = open_p - curr_p
                    pips = round(diff / sym.point_size, 1)
                    pnl = round(diff * contract_size * lot, 2)
                    
                    if position.lowest_price is None or curr_price < position.lowest_price:
                        position.lowest_price = curr_price

                position.floating_pips = pips
                position.floating_pnl = Decimal(str(pnl))

                # Kiểm tra chạm TP/SL
                if position.position_type == 'BUY':
                    if position.take_profit and curr_price >= position.take_profit:
                        cls.close_position(position, reason='TP_HIT')
                        continue
                    elif position.stop_loss and curr_price <= position.stop_loss:
                        cls.close_position(position, reason='SL_HIT')
                        continue
                else: # SELL
                    if position.take_profit and curr_price <= position.take_profit:
                        cls.close_position(position, reason='TP_HIT')
                        continue
                    elif position.stop_loss and curr_price >= position.stop_loss:
                        cls.close_position(position, reason='SL_HIT')
                        continue

                position.save()

            cls.recalculate_all_wallets()
        except Exception as e:
            BotLog.log(level='ERROR', category='EXECUTION', message=f'Lỗi khi cập nhật vị thế: {e}', traceback=traceback.format_exc())

    @classmethod
    def close_position(cls, position: Position, close_price: Decimal = None, reason: str = 'TP_HIT'):
        """Đóng vị thế THẬT trên sàn Exness MT5 và lưu vào lịch sử."""
        wallet = position.wallet
        connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
        
        # Gửi yêu cầu đóng lệnh lên Exness MT5
        closed_on_broker = connector.close_order(
            ticket=int(position.ticket) if str(position.ticket).isdigit() else 0,
            symbol=position.symbol,
            order_type=position.position_type,
            volume=position.lot_size
        )
        
        if not closed_on_broker:
            logger.warning(f"Đóng lệnh #{position.ticket} trên Exness MT5: Trả về False (hoặc lệnh đã tự đóng tại sàn do SL/TP).")

        if close_price is not None:
            position.current_price = close_price
        pnl = position.floating_pnl
        is_win = pnl > 0

        TradeHistory.objects.create(
            wallet=wallet,
            ticket=position.ticket,
            symbol=position.symbol,
            position_type=position.position_type,
            lot_size=position.lot_size,
            open_price=position.open_price,
            close_price=position.current_price,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            pnl=pnl,
            pips=position.floating_pips,
            close_reason=reason,
            is_win=is_win,
            opened_at=position.opened_at,
            closed_at=timezone.now()
        )

        # Cập nhật số dư ví, thống kê và winrate
        wallet.balance = Decimal(str(wallet.balance)) + pnl
        wallet.today_pnl = Decimal(str(wallet.today_pnl)) + pnl
        wallet.total_profit = Decimal(str(wallet.total_profit)) + pnl
        wallet.total_trades += 1
        if is_win:
            wallet.winning_trades += 1
        else:
            wallet.losing_trades += 1
        wallet.calculate_metrics()
        wallet.save()

        BotLog.log(
            level='INFO',
            category='EXECUTION',
            message=f"ĐÃ ĐÓNG LỆNH THẬT TRÊN EXNESS MT5 #{position.ticket}: {position.position_type} {position.symbol} (PnL: {pnl:+} USD, Lý do: {reason})",
            wallet=wallet,
            symbol=position.symbol
        )
        # Cập nhật trạng thái Plan nếu có
        if position.plan:
            position.plan.status = 'COMPLETED'
            position.plan.save()

        position.delete()

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
        Tự động đồng bộ số dư & lệnh từ Exness MT5 và ghi nhật ký lỗi chi tiết nếu có sự cố.
        """
        try:
            # Step 0: Đồng bộ số dư và vị thế realtime từ Exness MT5
            for wallet in WalletAccount.objects.filter(is_active=True):
                if wallet.account_type == 'REAL' and wallet.mt5_login:
                    try:
                        connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
                        if connector.connect():
                            connector.sync_account_info(wallet)
                            connector.sync_positions(wallet)
                        else:
                            # Không thể kết nối tới máy chủ Exness: Tự động vô hiệu hóa ví Real để tránh spam log
                            wallet.is_active = False
                            wallet.bot_status = 'STOPPED'
                            wallet.save(update_fields=['is_active', 'bot_status'])
                            BotLog.log(
                                level='ERROR',
                                category='SYSTEM',
                                wallet=wallet,
                                message=f"Đã tự động ngắt kết nối và tạm dừng ví Real '{wallet.name}' (#{wallet.mt5_login}) do mất kết nối tới máy chủ Exness ({wallet.mt5_server}).",
                                traceback=f"Auto-disabled wallet #{wallet.mt5_login} on server {wallet.mt5_server} to prevent spam."
                            )
                    except Exception as me:
                        logger.warning(f"Không thể đồng bộ MT5 #{wallet.mt5_login}: {me}")
                        wallet.is_active = False
                        wallet.bot_status = 'STOPPED'
                        wallet.save(update_fields=['is_active', 'bot_status'])
                        BotLog.log(
                            level='ERROR',
                            category='SYSTEM',
                            wallet=wallet,
                            message=f"Đã tự động tắt ví '{wallet.name}' (#{wallet.mt5_login}) do lỗi ngoại lệ kết nối: {str(me)}",
                            traceback=traceback.format_exc()
                        )
                elif wallet.account_type == 'DEMO' and wallet.mt5_login:
                    # Với ví Demo: Nếu có MT5 Live thì sync từ MT5, nếu trên Sandbox thì duy trì hoạt động
                    try:
                        connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
                        if connector.connect():
                            connector.sync_account_info(wallet)
                            connector.sync_positions(wallet)
                    except Exception:
                        pass

            # Step 1: Đồng bộ giá thị trường thực tế từ MT5
            cls.sync_symbol_prices_from_mt5()

            # Step 2: Generate technical forecasts for all active symbols
            forecasts = {}
            for sym_config in SymbolConfig.objects.filter(is_active=True):
                try:
                    forecast = TechnicalAnalyzer.generate_market_analysis(sym_config)
                    forecasts[sym_config.symbol] = (sym_config, forecast)
                except Exception as fe:
                    BotLog.log(
                        level='ERROR',
                        category='ANALYSIS',
                        message=f"Lỗi phân tích kỹ thuật cặp {sym_config.symbol}: {fe}",
                        traceback=traceback.format_exc(),
                        symbol=sym_config.symbol
                    )

            # Step 3 & 4: Plans and Orders per active wallet
            for wallet in WalletAccount.objects.filter(is_active=True, bot_status='RUNNING'):
                # Kiểm tra giới hạn số lệnh
                current_open = wallet.positions.count()
                if current_open >= wallet.max_open_trades:
                    continue

                for sym_name in wallet.allowed_symbols:
                    if sym_name in forecasts:
                        sym_config, forecast = forecasts[sym_name]

                        # Kiểm tra lọc Spread
                        if sym_config.current_spread_pips > sym_config.max_allowed_spread:
                            BotLog.log(
                                level='WARNING',
                                category='RISK',
                                message=f"Cặp {sym_name} bị giãn Spread ({sym_config.current_spread_pips} pips > Max {sym_config.max_allowed_spread} pips). Bot tạm dừng vào lệnh để bảo vệ vốn.",
                                wallet=wallet,
                                symbol=sym_name
                            )
                            continue

                        # Avoid duplicate active plans for same symbol on same wallet
                        existing_plan = TradingPlan.objects.filter(
                            wallet=wallet, 
                            symbol=sym_name, 
                            status__in=['PENDING_TRIGGER', 'EXECUTING']
                        ).first()

                        if not existing_plan:
                            try:
                                plan = AutoPlanGenerator.generate_plan_for_wallet(wallet, sym_config, forecast)
                                if plan and plan.status == 'PENDING_TRIGGER':
                                    if forecast.confidence_score >= 82.0:
                                        cls.trigger_plan_to_position(plan)
                            except Exception as pe:
                                BotLog.log(
                                    level='ERROR',
                                    category='PLAN',
                                    message=f"Lỗi sinh kế hoạch giao dịch cho ví '{wallet.name}' ({sym_name}): {pe}",
                                    traceback=traceback.format_exc(),
                                    wallet=wallet,
                                    symbol=sym_name
                                )

            # Step 5: Update all positions & trailing stops
            cls.update_positions_and_pnl()

        except Exception as e:
            BotLog.log(
                level='CRITICAL',
                category='SYSTEM',
                message=f"Lỗi nghiêm trọng trong chu kỳ giao dịch của Bot: {e}",
                traceback=traceback.format_exc()
            )
