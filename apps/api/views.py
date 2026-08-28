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
from apps.plans.planner import AutoPlanGenerator
from apps.trading.models import Position, TradeHistory, BotLog
from apps.trading.execution_engine import ExecutionEngine
from apps.analysis.analyzer import TechnicalAnalyzer

import time
from django.http import StreamingHttpResponse

def build_live_ticks_data():
    """Tạo payload đồng bộ giá Live, Vị thế, Lỗ/Lãi Thả Nổi, Số Dư, Vốn Khả Dụng và KPI thời gian thực."""
    try:
        from apps.trading.live_market_feed import LiveMarketFeedService
        from apps.trading.execution_engine import ExecutionEngine
        LiveMarketFeedService.sync_all_symbols()
        # Tính toán lại Floating PnL của từng vị thế và Equity của từng Ví theo giá Live
        ExecutionEngine.update_positions_and_pnl()
    except Exception:
        pass

    # 1. Symbols
    symbols = []
    for s in SymbolConfig.objects.filter(is_active=True):
        symbols.append({
            'symbol': s.symbol,
            'display_name': s.display_name,
            'category': s.category,
            'category_display': s.get_category_display(),
            'current_price': float(s.current_price),
            'bid': float(s.current_bid),
            'ask': float(s.current_ask),
            'spread': float(s.current_spread_pips),
            'timeframe': s.timeframe,
            'digits': s.digits,
        })

    # 2. Positions
    positions = []
    for p in Position.objects.select_related('wallet').all():
        positions.append({
            'id': p.id,
            'ticket': p.ticket,
            'wallet_id': p.wallet_id,
            'wallet_name': p.wallet.name if p.wallet else '',
            'symbol': p.symbol,
            'position_type': p.position_type,
            'lot_size': float(p.lot_size),
            'open_price': float(p.open_price),
            'current_price': float(p.current_price),
            'stop_loss': float(p.stop_loss),
            'take_profit': float(p.take_profit),
            'floating_pnl': float(p.floating_pnl),
            'floating_pips': float(p.floating_pips),
            'is_breakeven_set': p.is_breakeven_set,
            'is_trailing': p.is_trailing,
        })

    # 3. Wallets Summary
    wallets = []
    tot_bal = Decimal("0.00")
    tot_eq = Decimal("0.00")
    tot_fl = Decimal("0.00")
    tot_td = Decimal("0.00")
    tot_prof = Decimal("0.00")
    tot_trades = 0
    tot_wins = 0

    for w in WalletAccount.objects.all():
        tot_bal += w.balance
        tot_eq += w.equity
        tot_fl += w.floating_pnl
        tot_td += w.today_pnl
        tot_prof += w.total_profit
        tot_trades += w.total_trades
        tot_wins += w.winning_trades

        p_count = sum(1 for pos in positions if pos['wallet_id'] == w.id)
        wallets.append({
            'id': w.id,
            'name': w.name,
            'account_type': w.account_type,
            'account_type_display': w.get_account_type_display(),
            'mt5_login': w.mt5_login,
            'mt5_server': w.mt5_server,
            'capital': float(w.capital),
            'balance': float(w.balance),
            'equity': float(w.equity),
            'floating_pnl': float(w.floating_pnl),
            'today_pnl': float(w.today_pnl),
            'total_profit': float(w.total_profit),
            'win_rate': w.win_rate,
            'total_trades': w.total_trades,
            'active_trades_count': p_count,
            'risk_percent': w.risk_percent,
            'is_active': w.is_active,
            'bot_status': w.bot_status,
            'bot_status_display': w.get_bot_status_display(),
            'allowed_symbols': w.allowed_symbols,
        })

    winrate = round((tot_wins / tot_trades) * 100, 1) if tot_trades > 0 else 0.0

    return {
        'overview': {
            'total_balance': float(tot_bal),
            'total_equity': float(tot_eq),
            'total_floating_pnl': float(tot_fl),
            'total_today_pnl': float(tot_td),
            'total_profit': float(tot_prof),
            'overall_winrate': winrate,
            'active_positions_count': len(positions),
            'active_wallets_count': len([w for w in wallets if w['is_active']]),
            'total_wallets_count': len(wallets),
        },
        'symbols': symbols,
        'positions': positions,
        'wallets': wallets,
        'timestamp': timezone.now().strftime('%H:%M:%S')
    }

@api_view(['GET'])
def live_ticks_api(request):
    """API REST một lần trả về dữ liệu nhanh khi cần fallback hoặc tải trang."""
    return Response(build_live_ticks_data())

def stream_ticks_api(request):
    """
    Hook Stream Server-Sent Events (SSE) thời gian thực:
    - 0 request lặp lại (1 kết nối duy nhất, máy chủ tự động PUSH dữ liệu xuống khi có tick).
    - Cực kỳ tiết kiệm băng thông và tài nguyên CPU mạng.
    """
    def event_stream():
        while True:
            try:
                data = build_live_ticks_data()
                yield f"data: {json.dumps(data)}\n\n"
            except Exception:
                pass
            time.sleep(0.5)

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


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
            'capital': float(w.capital),
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
        'capital': float(w.capital),
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

@api_view(['POST'])
def admin_wallet_test_connection_api(request):
    """API kiểm tra kết nối trực tiếp tới máy chủ Exness MT5 trước khi lưu ví."""
    data = request.data
    login = data.get('mt5_login', '').strip()
    password = data.get('mt5_password', '').strip()
    server = data.get('mt5_server', '').strip()
    account_type = data.get('account_type', 'REAL').strip()
    wallet_id = data.get('wallet_id')

    if wallet_id and (not password or password == 'existing_password'):
        try:
            w = WalletAccount.objects.get(pk=wallet_id)
            password = w.mt5_password
            if not server:
                server = w.mt5_server
            if not login:
                login = w.mt5_login
        except WalletAccount.DoesNotExist:
            pass
    elif not password or password == 'existing_password':
        try:
            w = WalletAccount.objects.filter(mt5_login=login).first()
            if w:
                password = w.mt5_password
        except Exception:
            pass

    from apps.trading.mt5_connector import ExnessMT5Connector
    is_valid, msg, acc_info = ExnessMT5Connector.test_connection(
        login=login, 
        password=password, 
        server=server, 
        account_type=account_type
    )
    if not is_valid:
        return Response({'success': False, 'error': msg}, status=status.HTTP_400_BAD_REQUEST)

    return Response({
        'success': True,
        'message': msg,
        'account_info': acc_info
    })


@api_view(['POST', 'PUT', 'DELETE'])
def admin_wallet_manage_api(request, wallet_id=None):
    """Thêm/Sửa/Xóa/Bật-Tắt Ví Exness."""
    from apps.trading.mt5_connector import ExnessMT5Connector

    if request.method == 'POST':
        data = request.data
        name = data.get('name', '').strip()
        server_name = data.get('mt5_server', '').strip()
        mt5_login = data.get('mt5_login', '').strip()
        mt5_pass = data.get('mt5_password', '').strip()
        account_type = data.get('account_type', 'DEMO').strip()
        symbols = data.get('allowed_symbols', [])

        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(',') if s.strip()]

        if not name:
            return Response({'error': 'Tên ví không được để trống'}, status=status.HTTP_400_BAD_REQUEST)
        if not server_name:
            return Response({'error': 'Máy chủ Exness không được để trống'}, status=status.HTTP_400_BAD_REQUEST)
        if not mt5_login:
            return Response({'error': 'Số tài khoản MT5 không được để trống'}, status=status.HTTP_400_BAD_REQUEST)
        if not mt5_pass:
            return Response({'error': 'Mật khẩu MT5 không được để trống'}, status=status.HTTP_400_BAD_REQUEST)
        if not symbols or len(symbols) == 0:
            return Response({'error': 'Vui lòng chọn ít nhất một cặp giao dịch cho ví'}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Bắt buộc kiểm tra kết nối tới sàn Exness MT5 / Sandbox
        is_valid, msg, acc_info = ExnessMT5Connector.test_connection(
            login=mt5_login, 
            password=mt5_pass, 
            server=server_name, 
            account_type=account_type
        )
        if not is_valid:
            return Response({'error': f'Lỗi kết nối sàn Exness: {msg}'}, status=status.HTTP_400_BAD_REQUEST)

        if 'Trial' in server_name or 'Demo' in server_name or account_type == 'DEMO':
            detected_type = 'DEMO'
        else:
            detected_type = 'REAL'

        if 'capital' in data and data['capital'] is not None and str(data['capital']).strip() != '':
            init_cap = Decimal(str(data['capital']))
        elif 'balance' in data and data['balance'] is not None and str(data['balance']).strip() != '':
            init_cap = Decimal(str(data['balance']))
        else:
            init_cap = Decimal(str(acc_info.get('balance', 1000.00)))

        wallet = WalletAccount.objects.create(
            name=name,
            account_type=detected_type,
            mt5_login=mt5_login,
            mt5_password=mt5_pass,
            mt5_server=server_name,
            capital=init_cap,
            risk_percent=float(data.get('risk_percent', 1.5)),
            max_daily_loss_percent=float(data.get('max_daily_loss_percent', 4.0)),
            max_open_trades=int(data.get('max_open_trades', 5)),
            is_active=data.get('is_active', True),
            bot_status=data.get('bot_status', 'RUNNING')
        )
        wallet.set_allowed_symbols(symbols)
        wallet.save()

        # Tự động đồng bộ ngay số dư và lệnh trực tiếp từ sàn Exness MT5
        try:
            connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
            if connector.connect():
                connector.sync_account_info(wallet)
                connector.sync_positions(wallet)
        except Exception:
            pass

        # Tự động tính toán kế hoạch giao dịch AI mới dựa trên số dư & danh sách cặp cho phép mới
        created_plans = AutoPlanGenerator.refresh_plans_for_wallet(wallet)
        BotLog.log(
            level='INFO',
            category='PLAN',
            message=f"Đã sinh {len(created_plans)} kế hoạch giao dịch AI mới cho ví '{wallet.name}' dựa trên số dư ${wallet.balance:,.2f} và {len(wallet.allowed_symbols)} cặp giao dịch cho phép.",
            wallet=wallet
        )

        return Response({'success': True, 'message': 'Kết nối và tạo ví Exness thành công', 'wallet_id': wallet.id})

    elif request.method == 'PUT':
        try:
            wallet = WalletAccount.objects.get(pk=wallet_id)
        except WalletAccount.DoesNotExist:
            return Response({'error': 'Không tìm thấy ví'}, status=status.HTTP_404_NOT_FOUND)

        data = request.data
        if 'allowed_symbols' in data:
            symbols = data['allowed_symbols']
            if isinstance(symbols, str):
                symbols = [s.strip() for s in symbols.split(',') if s.strip()]
            if not symbols or len(symbols) == 0:
                return Response({'error': 'Vui lòng chọn ít nhất một cặp giao dịch cho ví'}, status=status.HTTP_400_BAD_REQUEST)
            wallet.set_allowed_symbols(symbols)

        new_server = data.get('mt5_server', wallet.mt5_server).strip()
        new_login = data.get('mt5_login', wallet.mt5_login).strip()
        new_pass = data.get('mt5_password', '').strip() or wallet.mt5_password

        # Nếu thay đổi thông tin kết nối, kiểm tra lại với sàn
        if ('mt5_server' in data or 'mt5_login' in data or ('mt5_password' in data and data['mt5_password'].strip())):
            is_valid, msg, acc_info = ExnessMT5Connector.test_connection(new_login, new_pass, new_server)
            if not is_valid:
                return Response({'error': f'Lỗi kết nối sàn Exness: {msg}'}, status=status.HTTP_400_BAD_REQUEST)

        if 'name' in data and data['name'].strip(): wallet.name = data['name'].strip()
        if 'capital' in data and data['capital'] is not None and str(data['capital']).strip() != '':
            wallet.capital = Decimal(str(data['capital']))
        elif 'balance' in data and data['balance'] is not None and str(data['balance']).strip() != '':
            wallet.capital = Decimal(str(data['balance']))
        if 'mt5_server' in data and data['mt5_server'].strip():
            wallet.mt5_server = data['mt5_server'].strip()
            if 'account_type' not in data:
                if 'Trial' in wallet.mt5_server or 'Demo' in wallet.mt5_server:
                    wallet.account_type = 'DEMO'
                elif 'Simulation' in wallet.mt5_server or 'Sandbox' in wallet.mt5_server:
                    wallet.account_type = 'SIMULATION'
                else:
                    wallet.account_type = 'REAL'
        if 'account_type' in data: wallet.account_type = data['account_type']
        if 'mt5_login' in data and data['mt5_login'].strip(): wallet.mt5_login = data['mt5_login'].strip()
        if 'mt5_password' in data and data['mt5_password'].strip(): wallet.mt5_password = data['mt5_password'].strip()
        if 'risk_percent' in data: wallet.risk_percent = float(data['risk_percent'])
        if 'max_daily_loss_percent' in data: wallet.max_daily_loss_percent = float(data['max_daily_loss_percent'])
        if 'max_open_trades' in data: wallet.max_open_trades = int(data['max_open_trades'])
        if 'is_active' in data: wallet.is_active = bool(data['is_active'])
        if 'bot_status' in data: wallet.bot_status = data['bot_status']

        wallet.save()

        # Tự động đồng bộ lại từ MT5
        try:
            connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
            if connector.connect():
                connector.sync_account_info(wallet)
                connector.sync_positions(wallet)
        except Exception:
            pass

        # Xóa các plan AI chưa khớp lệnh cũ & tính toán lại plan mới dựa trên số dư và danh sách cặp cho phép mới
        created_plans = AutoPlanGenerator.refresh_plans_for_wallet(wallet)
        BotLog.log(
            level='INFO',
            category='PLAN',
            message=f"Đã làm mới kế hoạch giao dịch AI cho ví '{wallet.name}': Xóa các plan cũ chưa khớp và tạo mới {len(created_plans)} plan theo số dư ${wallet.balance:,.2f} & {len(wallet.allowed_symbols)} cặp giao dịch.",
            wallet=wallet
        )

        return Response({'success': True, 'message': 'Cập nhật ví Exness thành công'})

    elif request.method == 'DELETE':
        try:
            wallet = WalletAccount.objects.get(pk=wallet_id)
            
            # 1. Ngắt kết nối và tắt Bot cho ví trước khi xóa
            wallet.is_active = False
            wallet.bot_status = 'STOPPED'
            wallet.save(update_fields=['is_active', 'bot_status'])
            
            # 2. Ngắt kết nối khỏi máy chủ MT5 nếu đang mở
            try:
                connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
                connector.disconnect()
            except Exception:
                pass
            
            # 3. Đóng hoặc dọn dẹp các vị thế đang gắn với ví
            try:
                wallet.positions.all().delete()
                wallet.plans.all().delete()
            except Exception:
                pass

            # 4. Ghi log ngắt kết nối an toàn (INFO)
            BotLog.log(
                level='INFO',
                category='SYSTEM',
                message=f"Đã ngắt kết nối máy chủ ({wallet.mt5_server}) và xóa an toàn ví '{wallet.name}' (#{wallet.mt5_login}) khỏi hệ thống."
            )

            # 5. Xóa ví khỏi cơ sở dữ liệu
            wallet.delete()
            return Response({'success': True, 'message': 'Đã ngắt kết nối máy chủ và xóa ví thành công'})
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
    BotLog.objects.all().delete()
    return Response({'success': True, 'message': 'Đã dọn dẹp sạch toàn bộ nhật ký!'})


# ==================== MASTER DATA APIS ====================

@api_view(['GET'])
def admin_master_data_api(request):
    """Lấy toàn bộ Master Data (Server Exness, Cặp Tiền)."""
    from apps.accounts.models import ExnessServerMaster
    from apps.symbols.models import SymbolConfig

    servers = []
    for s in ExnessServerMaster.objects.all():
        servers.append({
            'id': s.id,
            'server_name': s.server_name,
            'server_type': s.server_type,
            'server_type_display': s.get_server_type_display(),
            'description': s.description,
            'is_active': s.is_active,
            'order': s.order
        })

    symbols = []
    for sym in SymbolConfig.objects.all():
        symbols.append({
            'id': sym.id,
            'symbol': sym.symbol,
            'display_name': sym.display_name,
            'category': sym.category,
            'category_display': sym.get_category_display(),
            'is_active': sym.is_active
        })

    return Response({
        'servers': servers,
        'symbols': symbols
    })


@api_view(['POST', 'PUT', 'DELETE'])
def admin_servers_api(request, server_id=None):
    """Thêm / Sửa / Xóa Server Exness Master Data."""
    from apps.accounts.models import ExnessServerMaster

    if request.method == 'POST':
        data = request.data
        name = data.get('server_name', '').strip()
        if not name:
            return Response({'error': 'Tên server không được để trống'}, status=status.HTTP_400_BAD_REQUEST)

        server, created = ExnessServerMaster.objects.update_or_create(
            server_name=name,
            defaults={
                'server_type': data.get('server_type', 'REAL'),
                'description': data.get('description', ''),
                'is_active': bool(data.get('is_active', True)),
                'order': int(data.get('order', 50))
            }
        )
        msg = 'Đã thêm server mới' if created else 'Đã cập nhật server'
        return Response({'success': True, 'message': msg, 'server_id': server.id})

    elif request.method == 'PUT':
        try:
            server = ExnessServerMaster.objects.get(pk=server_id)
        except ExnessServerMaster.DoesNotExist:
            return Response({'error': 'Không tìm thấy server'}, status=status.HTTP_404_NOT_FOUND)

        data = request.data
        if 'server_name' in data: server.server_name = data['server_name'].strip()
        if 'server_type' in data: server.server_type = data['server_type']
        if 'description' in data: server.description = data['description']
        if 'is_active' in data: server.is_active = bool(data['is_active'])
        if 'order' in data: server.order = int(data['order'])
        server.save()
        return Response({'success': True, 'message': f'Đã cập nhật server {server.server_name}'})

    elif request.method == 'DELETE':
        try:
            server = ExnessServerMaster.objects.get(pk=server_id)
            server.delete()
            return Response({'success': True, 'message': 'Đã xóa server khỏi Master Data'})
        except ExnessServerMaster.DoesNotExist:
            return Response({'error': 'Không tìm thấy server'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def admin_change_password_api(request):
    """API cho phép Admin đổi mật khẩu trực tiếp trên giao diện Web (bắt buộc nhập mật khẩu cũ)."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return Response({'error': 'Bạn không có quyền thực hiện thao tác này'}, status=status.HTTP_403_FORBIDDEN)

    data = request.data
    old_password = data.get('old_password', '').strip()
    new_password = data.get('new_password', '').strip()
    confirm_password = data.get('confirm_password', '').strip()

    if not old_password:
        return Response({'error': 'Vui lòng nhập mật khẩu cũ hiện tại'}, status=status.HTTP_400_BAD_REQUEST)

    user = request.user
    if not user.check_password(old_password):
        return Response({'error': 'Mật khẩu cũ không chính xác'}, status=status.HTTP_400_BAD_REQUEST)

    if not new_password:
        return Response({'error': 'Mật khẩu mới không được để trống'}, status=status.HTTP_400_BAD_REQUEST)

    if len(new_password) < 4:
        return Response({'error': 'Mật khẩu mới phải có tối thiểu 4 ký tự'}, status=status.HTTP_400_BAD_REQUEST)

    if new_password == old_password:
        return Response({'error': 'Mật khẩu mới không được trùng với mật khẩu cũ hiện tại'}, status=status.HTTP_400_BAD_REQUEST)

    if new_password != confirm_password:
        return Response({'error': 'Xác nhận mật khẩu mới không khớp'}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(new_password)
    user.save()
    
    from django.contrib.auth import update_session_auth_hash
    update_session_auth_hash(request, user)

    return Response({'success': True, 'message': 'Đã đổi mật khẩu Admin thành công!'})


@api_view(['GET'])
def admin_code_logs_api(request):
    """Lấy danh sách nhật ký & báo lỗi code từ bảng CodeLog trong Database."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return Response({'error': 'Bạn không có quyền xem log code'}, status=status.HTTP_403_FORBIDDEN)

    from apps.trading.models import CodeLog
    level_filter = request.query_params.get('level', 'ALL').upper()

    logs_qs = CodeLog.objects.all()
    total_count = logs_qs.count()
    unresolved_count = logs_qs.filter(is_resolved=False).count()
    error_count = logs_qs.filter(level__in=['ERROR', 'CRITICAL']).count()
    warning_count = logs_qs.filter(level='WARNING').count()
    info_count = logs_qs.filter(level='INFO').count()

    if level_filter == 'ERROR':
        logs_qs = logs_qs.filter(level__in=['ERROR', 'CRITICAL'])
    elif level_filter in ['WARNING', 'INFO', 'CRITICAL']:
        logs_qs = logs_qs.filter(level=level_filter)

    logs_list = []
    for l in logs_qs[:200]:
        logs_list.append({
            'id': l.id,
            'level': l.level,
            'module': l.module,
            'line_number': l.line_number,
            'exception_type': l.exception_type,
            'message': l.message,
            'traceback': l.traceback,
            'request_path': l.request_path,
            'request_method': l.request_method,
            'is_resolved': l.is_resolved,
            'created_at': l.created_at.strftime('%H:%M:%S %d/%m/%Y')
        })

    return Response({
        'logs': logs_list,
        'total': total_count,
        'unresolved_errors': unresolved_count,
        'error_count': error_count,
        'warning_count': warning_count,
        'info_count': info_count
    })


@api_view(['POST'])
def admin_code_log_resolve_api(request, log_id):
    """Đánh dấu một lỗi code trong Database là đã xử lý."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return Response({'error': 'Bạn không có quyền thực hiện'}, status=status.HTTP_403_FORBIDDEN)

    from apps.trading.models import CodeLog
    try:
        log_obj = CodeLog.objects.get(pk=log_id)
        log_obj.is_resolved = True
        log_obj.save()
        return Response({'success': True, 'message': f'Đã đánh dấu xử lý lỗi code #{log_id}'})
    except CodeLog.DoesNotExist:
        return Response({'error': 'Không tìm thấy bản ghi lỗi code'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def admin_code_logs_clear_api(request):
    """Xóa sạch toàn bộ bản ghi lỗi code trong Database."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return Response({'error': 'Bạn không có quyền thực hiện'}, status=status.HTTP_403_FORBIDDEN)

    from apps.trading.models import CodeLog
    count = CodeLog.objects.count()
    CodeLog.objects.all().delete()
    return Response({'success': True, 'message': f'Đã xóa sạch {count} bản ghi nhật ký lỗi code trong Database'})





