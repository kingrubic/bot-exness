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

class ExecutionEngine:
    """
    Bộ máy thực thi quét thị trường, kích hoạt lệnh theo Plan,
    quản lý vị thế mở, Trailing Stop, Chốt Lời / Cắt Lỗ và cập nhật số dư ví.
    Tự động ghi nhật ký lỗi & cảnh báo vào BotLog để Admin theo dõi.
    """

    @classmethod
    def simulate_price_tick(cls):
        """Mô phỏng biến động giá nhỏ cho các cặp Exness đang kích hoạt."""
        try:
            for sym in SymbolConfig.objects.filter(is_active=True):
                price = float(sym.current_price)
                delta_pct = random.uniform(-0.0012, 0.0012)
                new_price = round(price * (1 + delta_pct), sym.digits)
                sym.current_price = Decimal(str(new_price))
                sym.current_bid = Decimal(str(round(new_price - (sym.point_size * 10), sym.digits)))
                sym.current_ask = Decimal(str(round(new_price + (sym.point_size * 10), sym.digits)))
                sym.save()
        except Exception as e:
            BotLog.log(level='ERROR', category='SYSTEM', message=f'Lỗi cập nhật giá nến: {e}', traceback=traceback.format_exc())

    @classmethod
    def trigger_plan_to_position(cls, plan: TradingPlan) -> Position:
        """Kích hoạt Trading Plan thành một Lệnh Mở (Position)."""
        ticket = f"{random.randint(10000000, 99999999)}"
        
        position = Position.objects.create(
            wallet=plan.wallet,
            plan=plan,
            ticket=ticket,
            symbol=plan.symbol,
            position_type=plan.direction,
            lot_size=plan.calculated_lot,
            open_price=plan.entry_price,
            current_price=plan.entry_price,
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
        BotLog.log(
            level='INFO',
            category='EXECUTION',
            message=f"Đã khớp lệnh #{ticket}: {position.position_type} {position.symbol} {position.lot_size} Lot tại giá {position.open_price} cho ví '{plan.wallet.name}'",
            wallet=plan.wallet,
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
                    if curr_price >= position.take_profit:
                        cls.close_position(position, reason='TP_HIT')
                        continue
                    elif curr_price <= position.stop_loss:
                        cls.close_position(position, reason='SL_HIT')
                        continue
                else: # SELL
                    if curr_price <= position.take_profit:
                        cls.close_position(position, reason='TP_HIT')
                        continue
                    elif curr_price >= position.stop_loss:
                        cls.close_position(position, reason='SL_HIT')
                        continue

                position.save()

            cls.recalculate_all_wallets()
        except Exception as e:
            BotLog.log(level='ERROR', category='EXECUTION', message=f'Lỗi khi cập nhật vị thế: {e}', traceback=traceback.format_exc())

    @classmethod
    def close_position(cls, position: Position, close_price: Decimal = None, reason: str = 'TP_HIT'):
        """Đóng một vị thế và lưu vào lịch sử."""
        wallet = position.wallet
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

        # Cập nhật số dư ví và thống kê
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

        # Cập nhật trạng thái Plan nếu có
        if position.plan:
            position.plan.status = 'COMPLETED'
            position.plan.save()

        # Ghi log đóng lệnh
        reason_text = "Chạm TP" if reason == 'TP_HIT' else ("Chạm SL" if reason == 'SL_HIT' else "Đóng thủ công")
        BotLog.log(
            level='INFO' if is_win else 'WARNING',
            category='EXECUTION',
            message=f"Đã đóng lệnh #{position.ticket} {position.symbol} ({reason_text}) -> PnL: {'+' if pnl >= 0 else ''}${pnl} cho ví '{wallet.name}'",
            wallet=wallet,
            symbol=position.symbol
        )

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
        Tự động ghi nhật ký lỗi chi tiết nếu có bất kỳ sự cố nào.
        """
        try:
            # Step 1: Simulate ticks
            cls.simulate_price_tick()

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
