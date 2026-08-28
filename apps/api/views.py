import json
from decimal import Decimal
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from apps.accounts.models import WalletAccount
from apps.symbols.models import SymbolConfig
from apps.analysis.models import MarketForecast
from apps.plans.models import TradingPlan
from apps.trading.models import Position, TradeHistory
from apps.trading.execution_engine import ExecutionEngine
from apps.analysis.analyzer import TechnicalAnalyzer

@api_view(['GET'])
def global_overview_api(request):
    """Báo cáo tổng hợp số liệu của tất cả các ví Exness."""
    wallets = WalletAccount.objects.all()
    
    total_balance = sum(w.balance for w in wallets)
    total_equity = sum(w.equity for w in wallets)
    total_floating = sum(w.floating_pnl for w in wallets)
    total_today = sum(w.today_pnl for w in wallets)
    total_profit = sum(w.total_profit for w in wallets)
    
    total_trades = sum(w.total_trades for w in wallets)
    total_wins = sum(w.winning_trades for w in wallets)
    overall_winrate = round((total_wins / total_trades) * 100, 1) if total_trades > 0 else 0.0
    
    active_positions_count = Position.objects.count()
    active_wallets_count = wallets.filter(is_active=True).count()
    
    data = {
        'total_balance': float(total_balance),
        'total_equity': float(total_equity),
        'total_floating_pnl': float(total_floating),
        'total_today_pnl': float(total_today),
        'total_profit': float(total_profit),
        'total_trades': total_trades,
        'overall_winrate': overall_winrate,
        'active_positions_count': active_positions_count,
        'active_wallets_count': active_wallets_count,
        'total_wallets_count': wallets.count(),
        'updated_at': timezone.now().strftime('%H:%M:%S %d/%m/%Y')
    }
    return Response(data)


@api_view(['GET'])
def wallet_list_api(request):
    """Danh sách các ví Exness kèm thông tin tóm tắt."""
    wallets = WalletAccount.objects.all()
    result = []
    for w in wallets:
        result.append({
            'id': w.id,
            'name': w.name,
            'account_type': w.account_type,
            'account_type_display': w.get_account_type_display(),
            'mt5_login': w.mt5_login,
            'mt5_server': w.mt5_server,
            'currency': w.currency,
            'leverage': w.leverage,
            'balance': float(w.balance),
            'equity': float(w.equity),
            'floating_pnl': float(w.floating_pnl),
            'today_pnl': float(w.today_pnl),
            'total_profit': float(w.total_profit),
            'win_rate': w.win_rate,
            'total_trades': w.total_trades,
            'active_trades_count': w.positions.count(),
            'risk_percent': w.risk_percent,
            'is_active': w.is_active,
            'bot_status': w.bot_status,
            'bot_status_display': w.get_bot_status_display(),
            'allowed_symbols': w.allowed_symbols,
        })
    return Response(result)


@api_view(['GET'])
def wallet_detail_api(request, wallet_id):
    """Chi tiết toàn diện của 1 ví: Report riêng, Phân tích dự báo tiếp theo, Plans, Positions, History."""
    try:
        w = WalletAccount.objects.get(pk=wallet_id)
    except WalletAccount.DoesNotExist:
        return Response({'error': 'Không tìm thấy ví'}, status=status.HTTP_404_NOT_FOUND)

    # 1. Performance Report for this wallet
    report = {
        'id': w.id,
        'name': w.name,
        'account_type': w.account_type,
        'account_type_display': w.get_account_type_display(),
        'mt5_login': w.mt5_login,
        'mt5_server': w.mt5_server,
        'currency': w.currency,
        'balance': float(w.balance),
        'equity': float(w.equity),
        'floating_pnl': float(w.floating_pnl),
        'today_pnl': float(w.today_pnl),
        'total_profit': float(w.total_profit),
        'win_rate': w.win_rate,
        'total_trades': w.total_trades,
        'winning_trades': w.winning_trades,
        'losing_trades': w.losing_trades,
        'profit_factor': w.profit_factor,
        'max_drawdown': w.max_drawdown,
        'risk_percent': w.risk_percent,
        'max_daily_loss_percent': w.max_daily_loss_percent,
        'is_active': w.is_active,
        'bot_status': w.bot_status,
        'bot_status_display': w.get_bot_status_display(),
        'allowed_symbols': w.allowed_symbols,
    }

    # 2. Forward Market Forecasts for the symbols enabled in this wallet
    forecasts_data = []
    for sym_name in w.allowed_symbols:
        forecast = MarketForecast.objects.filter(symbol=sym_name).first()
        sym_config = SymbolConfig.objects.filter(symbol=sym_name).first()
        if not forecast and sym_config:
            forecast = TechnicalAnalyzer.generate_market_analysis(sym_config)

        if forecast:
            forecasts_data.append({
                'symbol': forecast.symbol,
                'timeframe': forecast.timeframe,
                'trend_bias': forecast.trend_bias,
                'trend_bias_display': forecast.get_trend_bias_display(),
                'confidence_score': forecast.confidence_score,
                'current_price': float(forecast.current_price),
                'projected_target_zone': forecast.projected_target_zone,
                'next_resistance_1': float(forecast.next_resistance_1),
                'next_resistance_2': float(forecast.next_resistance_2),
                'next_support_1': float(forecast.next_support_1),
                'next_support_2': float(forecast.next_support_2),
                'trigger_condition': forecast.trigger_condition,
                'smc_structure': forecast.smc_structure,
                'analysis_rationale': forecast.analysis_rationale,
                'recommended_action': forecast.recommended_action,
                'recommended_action_display': forecast.get_recommended_action_display(),
                'indicators': forecast.indicators,
                'updated_at': forecast.updated_at.strftime('%H:%M:%S %d/%m/%Y'),
            })

    # 3. Trading Plans of this wallet
    plans_data = []
    for p in w.trading_plans.all()[:20]:
        plans_data.append({
            'id': p.id,
            'symbol': p.symbol,
            'timeframe': p.timeframe,
            'direction': p.direction,
            'entry_price': float(p.entry_price),
            'entry_zone': f"{p.entry_zone_low} - {p.entry_zone_high}",
            'stop_loss': float(p.stop_loss),
            'take_profit_1': float(p.take_profit_1),
            'take_profit_2': float(p.take_profit_2),
            'rr_ratio': p.rr_ratio,
            'calculated_lot': p.calculated_lot,
            'risk_amount_usd': float(p.risk_amount_usd),
            'rationale': p.rationale,
            'status': p.status,
            'status_display': p.get_status_display(),
            'created_at': p.created_at.strftime('%H:%M:%S %d/%m/%Y'),
        })

    # 4. Active Open Positions of this wallet
    positions_data = []
    for pos in w.positions.all():
        positions_data.append({
            'id': pos.id,
            'ticket': pos.ticket,
            'symbol': pos.symbol,
            'position_type': pos.position_type,
            'lot_size': pos.lot_size,
            'open_price': float(pos.open_price),
            'current_price': float(pos.current_price),
            'stop_loss': float(pos.stop_loss),
            'take_profit': float(pos.take_profit),
            'floating_pnl': float(pos.floating_pnl),
            'floating_pips': pos.floating_pips,
            'is_trailing': pos.is_trailing,
            'is_breakeven_set': pos.is_breakeven_set,
            'opened_at': pos.opened_at.strftime('%H:%M:%S %d/%m/%Y'),
        })

    # 5. Closed Trade History of this wallet
    history_data = []
    for h in w.trade_history.all()[:50]:
        history_data.append({
            'id': h.id,
            'ticket': h.ticket,
            'symbol': h.symbol,
            'position_type': h.position_type,
            'lot_size': h.lot_size,
            'open_price': float(h.open_price),
            'close_price': float(h.close_price),
            'stop_loss': float(h.stop_loss),
            'take_profit': float(h.take_profit),
            'pnl': float(h.pnl),
            'pips': h.pips,
            'close_reason': h.close_reason,
            'close_reason_display': h.get_close_reason_display(),
            'is_win': h.is_win,
            'opened_at': h.opened_at.strftime('%H:%M:%S %d/%m/%Y'),
            'closed_at': h.closed_at.strftime('%H:%M:%S %d/%m/%Y'),
        })

    return Response({
        'report': report,
        'forecasts': forecasts_data,
        'plans': plans_data,
        'positions': positions_data,
        'history': history_data
    })


@api_view(['POST'])
def close_position_api(request, position_id):
    """Đóng thủ công 1 lệnh đang mở."""
    try:
        pos = Position.objects.get(pk=position_id)
        wallet = pos.wallet
        ExecutionEngine.close_position(pos, reason='MANUAL_CLOSE')
        return Response({'success': True, 'message': f'Đã đóng thành công lệnh #{pos.ticket}'})
    except Position.DoesNotExist:
        return Response({'error': 'Không tìm thấy vị thế'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def close_all_positions_api(request, wallet_id=None):
    """Đóng tất cả các lệnh của 1 ví hoặc tất cả các ví (Khẩn cấp)."""
    if wallet_id:
        positions = Position.objects.filter(wallet_id=wallet_id)
    else:
        positions = Position.objects.all()

    count = positions.count()
    for pos in list(positions):
        ExecutionEngine.close_position(pos, reason='MANUAL_CLOSE')

    return Response({'success': True, 'message': f'Đã đóng toàn bộ {count} lệnh khẩn cấp!'})


@api_view(['POST'])
def trigger_trading_cycle_api(request):
    """Kích hoạt ngay 1 chu kỳ quét giá & tự động khớp lệnh của Bot."""
    ExecutionEngine.run_full_trading_cycle()
    return Response({'success': True, 'message': 'Đã hoàn tất 1 chu kỳ phân tích & khớp lệnh'})


# ==================== ADMIN CRUD APIS ====================

@api_view(['POST', 'PUT', 'DELETE'])
def admin_wallet_manage_api(request, wallet_id=None):
    """Thêm/Sửa/Xóa/Bật-Tắt Ví Exness."""
    if request.method == 'POST':
        data = request.data
        symbols = data.get('allowed_symbols', ['XAUUSD'])
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(',') if s.strip()]

        wallet = WalletAccount.objects.create(
            name=data.get('name', 'Ví Mới'),
            account_type=data.get('account_type', 'DEMO'),
            mt5_login=data.get('mt5_login', '12345678'),
            mt5_password=data.get('mt5_password', ''),
            mt5_server=data.get('mt5_server', 'Exness-MT5Real'),
            initial_balance=Decimal(str(data.get('balance', 10000.00))),
            balance=Decimal(str(data.get('balance', 10000.00))),
            equity=Decimal(str(data.get('balance', 10000.00))),
            risk_percent=float(data.get('risk_percent', 1.5)),
            max_daily_loss_percent=float(data.get('max_daily_loss_percent', 4.0)),
            max_open_trades=int(data.get('max_open_trades', 5)),
            is_active=data.get('is_active', True),
            bot_status=data.get('bot_status', 'RUNNING')
        )
        wallet.set_allowed_symbols(symbols)
        wallet.save()
        return Response({'success': True, 'message': 'Tạo ví mới thành công', 'wallet_id': wallet.id})

    elif request.method == 'PUT':
        try:
            wallet = WalletAccount.objects.get(pk=wallet_id)
        except WalletAccount.DoesNotExist:
            return Response({'error': 'Không tìm thấy ví'}, status=status.HTTP_404_NOT_FOUND)

        data = request.data
        if 'name' in data: wallet.name = data['name']
        if 'account_type' in data: wallet.account_type = data['account_type']
        if 'mt5_login' in data: wallet.mt5_login = data['mt5_login']
        if 'mt5_server' in data: wallet.mt5_server = data['mt5_server']
        if 'risk_percent' in data: wallet.risk_percent = float(data['risk_percent'])
        if 'max_daily_loss_percent' in data: wallet.max_daily_loss_percent = float(data['max_daily_loss_percent'])
        if 'max_open_trades' in data: wallet.max_open_trades = int(data['max_open_trades'])
        if 'is_active' in data: wallet.is_active = bool(data['is_active'])
        if 'bot_status' in data: wallet.bot_status = data['bot_status']
        if 'allowed_symbols' in data:
            symbols = data['allowed_symbols']
            if isinstance(symbols, str):
                symbols = [s.strip() for s in symbols.split(',') if s.strip()]
            wallet.set_allowed_symbols(symbols)

        wallet.save()
        return Response({'success': True, 'message': 'Cập nhật ví thành công'})

    elif request.method == 'DELETE':
        try:
            wallet = WalletAccount.objects.get(pk=wallet_id)
            wallet.delete()
            return Response({'success': True, 'message': 'Đã xóa ví thành công'})
        except WalletAccount.DoesNotExist:
            return Response({'error': 'Không tìm thấy ví'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['GET', 'POST', 'PUT'])
def admin_symbols_api(request, symbol_id=None):
    """Quản lý các cặp giao dịch Exness."""
    if request.method == 'GET':
        symbols = SymbolConfig.objects.all()
        result = []
        for s in symbols:
            result.append({
                'id': s.id,
                'symbol': s.symbol,
                'display_name': s.display_name,
                'category': s.category,
                'category_display': s.get_category_display(),
                'timeframe': s.timeframe,
                'strategy': s.strategy,
                'strategy_display': s.get_strategy_display(),
                'current_price': float(s.current_price),
                'current_spread_pips': s.current_spread_pips,
                'max_allowed_spread': s.max_allowed_spread,
                'is_active': s.is_active,
                'last_scanned': s.last_scanned_at.strftime('%H:%M:%S')
            })
        return Response(result)

    elif request.method == 'POST':
        data = request.data
        sym = SymbolConfig.objects.create(
            symbol=data.get('symbol').upper().strip(),
            display_name=data.get('display_name', data.get('symbol')),
            category=data.get('category', 'FOREX'),
            timeframe=data.get('timeframe', 'M15'),
            strategy=data.get('strategy', 'SMC_TREND'),
            current_price=Decimal(str(data.get('current_price', 1.00))),
            is_active=data.get('is_active', True)
        )
        return Response({'success': True, 'message': f'Đã thêm cặp {sym.symbol}', 'id': sym.id})

    elif request.method == 'PUT':
        try:
            sym = SymbolConfig.objects.get(pk=symbol_id)
            data = request.data
            if 'timeframe' in data: sym.timeframe = data['timeframe']
            if 'strategy' in data: sym.strategy = data['strategy']
            if 'is_active' in data: sym.is_active = bool(data['is_active'])
            if 'max_allowed_spread' in data: sym.max_allowed_spread = float(data['max_allowed_spread'])
            sym.save()
            return Response({'success': True, 'message': f'Cập nhật thành công cặp {sym.symbol}'})
        except SymbolConfig.DoesNotExist:
            return Response({'error': 'Không tìm thấy cặp'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def run_backtest_api(request):
    """API chạy Backtest dữ liệu lịch sử cho các cặp Exness."""
    from apps.backtest.engine import BacktestEngine
    data = request.data
    symbol = data.get('symbol', 'XAUUSD')
    strategy = data.get('strategy', 'SMC_TREND')
    balance = float(data.get('initial_balance', 10000.0))
    days = int(data.get('days', 30))

    result = BacktestEngine.run_backtest(
        symbol=symbol,
        strategy=strategy,
        initial_balance=balance,
        days=days
    )
    return Response(result)


# ==================== BOT LOGS & ERROR MONITOR APIS ====================

@api_view(['GET'])
def admin_logs_api(request):
    """API lấy danh sách nhật ký, cảnh báo và lỗi hệ thống của Bot."""
    from apps.trading.models import BotLog
    level_filter = request.GET.get('level', 'ALL')
    
    qs = BotLog.objects.all()
    if level_filter != 'ALL':
        qs = qs.filter(level=level_filter)
        
    total_logs = qs.count()
    unresolved_errors = BotLog.objects.filter(level__in=['ERROR', 'CRITICAL'], is_resolved=False).count()
    
    logs_data = []
    for l in qs[:100]:
        logs_data.append({
            'id': l.id,
            'level': l.level,
            'category': l.category,
            'category_display': l.get_category_display(),
            'wallet_name': l.wallet.name if l.wallet else None,
            'wallet_id': l.wallet.id if l.wallet else None,
            'symbol': l.symbol,
            'message': l.message,
            'traceback': l.traceback,
            'is_resolved': l.is_resolved,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
        })

    return Response({
        'total': total_logs,
        'unresolved_errors': unresolved_errors,
        'logs': logs_data
    })


@api_view(['POST'])
def admin_log_resolve_api(request, log_id):
    """Đánh dấu lỗi đã được Admin xử lý."""
    from apps.trading.models import BotLog
    try:
        log = BotLog.objects.get(pk=log_id)
        log.is_resolved = True
        log.save()
        return Response({'success': True, 'message': 'Đã đánh dấu xử lý thành công!'})
    except BotLog.DoesNotExist:
        return Response({'error': 'Không tìm thấy log'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def admin_logs_clear_api(request):
    """Xóa toàn bộ nhật ký đã lưu."""
    from apps.trading.models import BotLog
    BotLog.objects.all().delete()
    return Response({'success': True, 'message': 'Đã dọn dẹp sạch toàn bộ nhật ký!'})


