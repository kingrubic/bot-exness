from apps.plans.entry_checks import entry_preview
import json
from decimal import Decimal
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Sum, Count, Q
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
from apps.core.time_utils import format_vn_time


def _optional_usd(data, key):
    """None nếu không gửi / trống / <= 0 — min TP và max SL được phép bỏ trống."""
    if key not in data:
        return Ellipsis
    raw = data.get(key)
    if raw is None or str(raw).strip() == '':
        return None
    try:
        v = Decimal(str(raw))
    except Exception:
        return None
    if v <= 0:
        return None
    return v


def _usd_or_none(val):
    if val is None:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    return round(v, 2) if v > 0 else None


def _mt5_status_with_wallets(use_cache=True):
    from apps.trading.mt5_launcher import MT5Launcher
    data = MT5Launcher.get_runtime_status(probe_api=True, timeout_ms=2000, use_cache=use_cache)
    data['wallet_active'] = WalletAccount.objects.filter(is_active=True).exists()
    data['wallet_running'] = WalletAccount.objects.filter(is_active=True, bot_status='RUNNING').exists()
    return data


def _live_wallet_money(w, acc, login):
    """Số dư/equity hiện trên UI: ví trùng login MT5 lấy từ terminal (kể cả $0)."""
    if acc is not None and login and str(getattr(w, 'mt5_login', '') or '') == str(login):
        return (
            Decimal(str(round(float(acc.balance or 0), 2))),
            Decimal(str(round(float(acc.equity or 0), 2))),
            Decimal(str(round(float(getattr(acc, 'profit', 0) or 0), 2))),
        )
    return w.balance, w.equity, w.floating_pnl


def _mt5_today_snap():
    try:
        from apps.trading.mt5_session import MT5NativeSession
        if not MT5NativeSession.available():
            return {'ok': False}, ''
        acc = MT5NativeSession.account()
        snap = MT5NativeSession.today_realized_pnl()
        login = str(acc.login) if acc else ''
        return snap, login
    except Exception:
        return {'ok': False}, ''


def _other_active_wallet(exclude_id=None):
    qs = WalletAccount.objects.filter(is_active=True)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return qs.first()


def _active_wallet_busy_error(other):
    return (
        f'Đã có ví đang kích hoạt: {other.name} (#{other.mt5_login}). '
        'MT5 chỉ chạy 1 tài khoản. Hãy tắt ví đó trước khi bật ví này.'
    )


def _empty_source_metrics():
    return {
        'total_trades': 0,
        'winning_trades': 0,
        'losing_trades': 0,
        'win_rate': 0.0,
        'total_profit': 0.0,
        'today_pnl': 0.0,
        'total_volume': 0.0,
    }


def _mt5_today_for_wallets(wallets):
    snap, login = _mt5_today_snap()
    if not snap.get('ok') or not login:
        return {'ok': False}, ''
    if not any(str(getattr(w, 'mt5_login', '') or '') == login for w in wallets):
        return {'ok': False}, login
    return snap, login


def _serialize_close_result(ticket, *, symbol='', position_type='', lot_size=0, pnl=0, close_reason=''):
    pnl_f = round(float(pnl or 0), 2)
    return {
        'ticket': str(ticket),
        'symbol': symbol or '',
        'position_type': position_type or '',
        'lot_size': float(lot_size or 0),
        'pnl': pnl_f,
        'is_win': pnl_f > 0,
        'close_reason': close_reason or '',
        'close_reason_display': _history_reason_display(close_reason),
    }


def _snapshot_open_close_result(pos):
    gross = float(getattr(pos, 'floating_pnl', 0) or 0)
    comm = float(getattr(pos, 'commission', 0) or 0)
    swap = float(getattr(pos, 'swap', 0) or 0)
    return _serialize_close_result(
        pos.ticket,
        symbol=pos.symbol,
        position_type=pos.position_type,
        lot_size=pos.lot_size,
        pnl=gross + comm + swap,
        close_reason='MANUAL_CLOSE',
    )


def _close_result_for_ticket(ticket, fallback=None):
    h = TradeHistory.objects.filter(ticket=str(ticket)).order_by('-id').first()
    if not h:
        return fallback
    return _serialize_close_result(
        h.ticket,
        symbol=h.symbol,
        position_type=h.position_type,
        lot_size=h.lot_size,
        pnl=h.pnl,
        close_reason=h.close_reason,
    )


def _history_reason_display(code):
    return {
        'TP_HIT': 'Chạm Take Profit (TP Hit)',
        'SL_HIT': 'Cắt Lỗ Tự Động (SL Hit)',
        'MANUAL_CLOSE': 'Đóng Thủ Công (Manual Close)',
        'TRAILING_TP': 'Chốt Lời Thoái Lui Đỉnh (Trailing TP)',
        'TREND_REVERSAL': 'Chốt Lời Khi Đảo Chiều Trend',
        'STOP_OUT': 'Thanh lý / Stop Out (sàn buộc đóng)',
        'MAX_DAILY_DD': 'Thanh lý / Stop Out (sàn buộc đóng)',
        'MARGIN_SAFETY_SL': 'Cắt Lỗ Cứu Ký Quỹ Ví (Margin Safety)',
    }.get(code, code or '')


def _serialize_mt5_history_row(row, wallet):
    src = row.get('source') or 'USER'
    return {
        'id': row.get('ticket'),
        'ticket': row.get('ticket'),
        'wallet_id': wallet.id if wallet else None,
        'wallet_name': wallet.name if wallet else 'MT5',
        'symbol': row.get('symbol'),
        'position_type': row.get('position_type'),
        'lot_size': float(row.get('lot_size') or 0.01),
        'open_price': float(row.get('open_price') or 0),
        'close_price': float(row.get('close_price') or 0),
        'stop_loss': None,
        'take_profit': None,
        'pnl': float(row.get('pnl') or 0),
        'commission': float(row.get('commission') or 0),
        'swap': float(row.get('swap') or 0),
        'pips': float(row.get('pips') or 0),
        'close_reason': row.get('close_reason') or 'TP_HIT',
        'close_reason_display': _history_reason_display(row.get('close_reason')),
        'is_win': bool(row.get('is_win')),
        'source': src,
        'source_display': 'BOT (Tự Động)' if src == 'BOT' else 'USER (Người Dùng)',
        'comment': row.get('comment') or '',
        'opened_at': format_vn_time(row.get('opened_at')),
        'closed_at': format_vn_time(row.get('closed_at')),
    }


def _mt5_closed_history_for_wallets(wallets, limit=100):
    """Lịch sử đóng lệnh trong ngày từ MT5."""
    try:
        from apps.trading.mt5_session import MT5NativeSession
        snap, login = _mt5_today_snap()
        if not MT5NativeSession.available() or not login:
            return None, None
        matched = [w for w in wallets if str(getattr(w, 'mt5_login', '') or '') == login]
        if not matched:
            return None, None
        rows = MT5NativeSession.closed_history(days=1, limit=limit)
        wallet = matched[0]
        return [_serialize_mt5_history_row(r, wallet) for r in rows], rows
    except Exception:
        return None, None


def _algo_warning_fields():
    st = _mt5_status_with_wallets(use_cache=False)
    need = bool(st.get('running') and st.get('logged_in') and not st.get('algo_trading'))
    if not need:
        return {'algo_required': False}
    return {
        'algo_required': True,
        'algo_title': st.get('title') or 'Cần bật Algo Trading trên MT5',
        'algo_steps': st.get('steps') or [],
    }


def _wallet_metrics_lite(w, today_bot=0.0, today_user=0.0, today_all=0.0):
    """Fallback rỗng — báo cáo BOT vs USER phải dùng get_daily_breakdown()."""
    empty = _empty_source_metrics()
    return {
        'all': {**empty, 'today_pnl': round(float(today_all or 0), 2)},
        'bot': {**empty, 'today_pnl': round(float(today_bot or 0), 2)},
        'user': {**empty, 'today_pnl': round(float(today_user or 0), 2)},
    }


def build_live_ticks_data():
    """Payload realtime nhẹ: giá/ví/vị thế. Không gom history dài ngày mỗi poll."""
    from apps.trading.mt5_session import MT5NativeSession

    # 1. Symbols từ cache tick
    symbols = []
    live_ticks = MT5NativeSession.cached_ticks()
    for s in SymbolConfig.objects.filter(is_active=True).only(
        'symbol', 'display_name', 'category', 'current_price', 'current_bid',
        'current_ask', 'current_spread_pips', 'timeframe', 'digits', 'is_active',
    ):
        tick = live_ticks.get(s.symbol) or {}
        symbols.append({
            'symbol': s.symbol,
            'display_name': s.display_name,
            'category': s.category,
            'category_display': s.get_category_display(),
            'current_price': float(tick.get('last') if tick else s.current_price),
            'bid': float(tick.get('bid') if tick else s.current_bid),
            'ask': float(tick.get('ask') if tick else s.current_ask),
            'spread': float(tick.get('spread_pips') if tick else s.current_spread_pips),
            'timeframe': s.timeframe,
            'digits': s.digits,
            'is_active': s.is_active,
        })

    def _pos_price(symbol, raw):
        s = str(symbol or '')
        n = float(raw or 0.0)
        if any(x in s for x in ('EUR', 'GBP', 'JPY')):
            return round(n, 5)
        return round(n, 2)

    current_wallet = WalletAccount.get_current()
    live_rows = []
    live_map = {}
    live_acc = None
    mt5_login = ''
    try:
        if MT5NativeSession.available():
            live_acc = MT5NativeSession.account()
            mt5_login = str(getattr(live_acc, 'login', '') or '') if live_acc else ''
            live_rows = MT5NativeSession.positions() or []
            live_map = {str(p.ticket): p for p in live_rows}
    except Exception:
        live_rows, live_map, live_acc, mt5_login = [], {}, None, ''

    # 2. Positions: ưu tiên live MT5 (nhanh, đủ khi DB lệch); giới hạn 150 dòng UI
    positions = []
    db_by_ticket = {}
    if current_wallet:
        for p in Position.objects.filter(wallet=current_wallet).select_related('wallet').order_by('opened_at', 'ticket')[:300]:
            db_by_ticket[str(p.ticket)] = p

    if live_rows and current_wallet and mt5_login and str(current_wallet.mt5_login or '') == mt5_login:
        for live in live_rows[:150]:
            ticket = str(live.ticket)
            dbp = db_by_ticket.get(ticket)
            ptype = 'BUY' if int(getattr(live, 'type', 0) or 0) == 0 else 'SELL'
            src = getattr(dbp, 'source', None) if dbp else None
            if src not in ('BOT', 'USER'):
                magic = int(getattr(live, 'magic', 0) or 0)
                src = 'BOT' if magic == 8882026 else 'USER'
            sym = str(getattr(live, 'symbol', '') or '')
            try:
                from apps.trading.mt5_connector import ExnessMT5Connector
                sym = ExnessMT5Connector.normalize_symbol(sym) or sym
            except Exception:
                pass
            positions.append({
                'id': dbp.id if dbp else ticket,
                'ticket': ticket,
                'wallet_id': current_wallet.id,
                'wallet_name': current_wallet.name,
                'symbol': sym,
                'position_type': ptype,
                'lot_size': float(getattr(live, 'volume', 0.01) or 0.01),
                'open_price': _pos_price(sym, getattr(live, 'price_open', 0)),
                'current_price': _pos_price(sym, getattr(live, 'price_current', 0)),
                'stop_loss': float(live.sl) if getattr(live, 'sl', 0) else None,
                'take_profit': float(live.tp) if getattr(live, 'tp', 0) else None,
                'floating_pnl': round(float(getattr(live, 'profit', 0) or 0), 2),
                'floating_pips': float(getattr(dbp, 'floating_pips', 0) or 0) if dbp else 0.0,
                'source': src,
                'source_display': 'BOT (Tự Động)' if src == 'BOT' else 'USER (Người Dùng)',
                'comment': str(getattr(live, 'comment', '') or ''),
                'is_breakeven_set': bool(getattr(dbp, 'is_breakeven_set', False)) if dbp else False,
                'is_trailing': bool(getattr(dbp, 'is_trailing', False)) if dbp else False,
            })
    elif current_wallet:
        for p in list(db_by_ticket.values())[:150]:
            live = live_map.get(str(p.ticket))
            cur = float(getattr(live, 'price_current', 0) or p.current_price or 0) if live else float(p.current_price or 0)
            pnl = float(getattr(live, 'profit', 0) or 0) if live else float(p.floating_pnl or 0)
            src = getattr(p, 'source', 'BOT') or 'BOT'
            positions.append({
                'id': p.id,
                'ticket': str(p.ticket),
                'wallet_id': p.wallet_id,
                'wallet_name': p.wallet.name if p.wallet else '',
                'symbol': p.symbol,
                'position_type': p.position_type,
                'lot_size': float(p.lot_size or 0.01),
                'open_price': _pos_price(p.symbol, p.open_price),
                'current_price': _pos_price(p.symbol, cur),
                'stop_loss': float(p.stop_loss) if p.stop_loss is not None else None,
                'take_profit': float(p.take_profit) if p.take_profit is not None else None,
                'floating_pnl': round(pnl, 2),
                'floating_pips': float(p.floating_pips or 0.0),
                'source': src,
                'source_display': 'BOT (Tự Động)' if src == 'BOT' else 'USER (Người Dùng)',
                'comment': getattr(p, 'comment', ''),
                'is_breakeven_set': p.is_breakeven_set,
                'is_trailing': p.is_trailing,
            })

    # 3. Wallets — BOT vs USER theo ngày GMT+7 (không gắn all-time vào BOT)
    from apps.core.time_utils import local_day_bounds
    wallets = []
    tot_bal = tot_eq = tot_fl = tot_td = Decimal('0.00')
    tot_prof = Decimal('0.00')
    tot_trades = tot_wins = 0
    daily_by_id = {}
    for w in WalletAccount.objects.all().only(
        'id', 'name', 'account_type', 'mt5_login', 'mt5_server', 'currency', 'capital',
        'balance_db', 'equity_db', 'floating_pnl', 'margin', 'margin_free', 'margin_level',
        'leverage', 'today_pnl', 'total_profit', 'win_rate', 'total_trades', 'winning_trades',
        'losing_trades', 'default_lot_size', 'max_open_trades',
        'min_take_profit_usd', 'max_stop_loss_usd', 'trail_sl_enabled', 'trail_sl_lock_usd',
        'is_active', 'bot_status',
        'allowed_symbols_json',
    ):
        daily = w.get_daily_breakdown()
        daily_by_id[w.id] = daily
        w_today = Decimal(str(daily['all']['today_pnl']))
        w_bal, w_eq, w_fl = _live_wallet_money(w, live_acc, mt5_login)
        breakdown = daily
        if current_wallet and w.id == current_wallet.id:
            tot_bal, tot_eq, tot_fl, tot_td = w_bal, w_eq, w_fl, w_today
            tot_prof = w.total_profit or Decimal('0')
            tot_trades = int(w.total_trades or 0)
            tot_wins = int(w.winning_trades or 0)
        p_count = len(positions) if (current_wallet and w.id == current_wallet.id) else 0
        if current_wallet and w.id == current_wallet.id and live_rows and mt5_login == str(w.mt5_login or ''):
            p_count = len(live_rows)
        wallets.append({
            'id': w.id,
            'name': w.name,
            'account_type': w.account_type,
            'account_type_display': w.get_account_type_display(),
            'mt5_login': w.mt5_login,
            'mt5_server': w.mt5_server,
            'currency': w.currency,
            'capital': float(w.capital),
            'balance': float(w_bal),
            'equity': float(w_eq),
            'floating_pnl': float(w_fl),
            'margin': float(w.margin or 0),
            'margin_free': float(w.margin_free or 0),
            'margin_level': float(w.margin_level or 0),
            'leverage': w.leverage,
            'leverage_display': w.leverage_display,
            'today_pnl': float(w_today),
            'total_profit': float(w.total_profit or 0),
            'win_rate': w.win_rate,
            'total_trades': w.total_trades,
            'active_trades_count': p_count,
            'default_lot_size': float(w.default_lot_size or 0.01),
            'max_open_trades': int(w.max_open_trades or 1),
            'min_take_profit_usd': _usd_or_none(w.min_take_profit_usd),
            'max_stop_loss_usd': _usd_or_none(getattr(w, 'max_stop_loss_usd', None)),
            'trail_sl_enabled': bool(getattr(w, 'trail_sl_enabled', False)),
            'trail_sl_lock_usd': _usd_or_none(getattr(w, 'trail_sl_lock_usd', None)),
            'is_active': w.is_active,
            'bot_status': w.bot_status,
            'bot_status_display': w.get_bot_status_display(),
            'allowed_symbols': w.allowed_symbols,
            'bot_metrics': breakdown['bot'],
            'user_metrics': breakdown['user'],
        })

    winrate = round((tot_wins / tot_trades) * 100, 1) if tot_trades > 0 else 0.0

    # 4. Plans chờ + Forecast suy nghĩ bot (realtime)
    plans = []
    plan_qs = TradingPlan.objects.filter(
        status__in=['PENDING_TRIGGER', 'PENDING', 'ANALYZING']
    ).select_related('wallet').order_by('-updated_at', '-created_at')
    if current_wallet:
        plan_qs = plan_qs.filter(wallet=current_wallet)
    else:
        plan_qs = plan_qs.none()
    for pl in plan_qs[:20]:
        plans.append({
            'id': pl.id,
            'wallet_id': pl.wallet_id,
            'wallet_name': pl.wallet.name if pl.wallet else '',
            'symbol': pl.symbol,
            'timeframe': pl.timeframe,
            'direction': pl.direction,
            'entry_price': float(pl.entry_price or 0.0),
            'entry_zone': 'MARKET',
            'stop_loss': float(pl.stop_loss) if pl.stop_loss is not None else None,
            'take_profit_1': float(pl.take_profit_1) if pl.take_profit_1 is not None else None,
            'take_profit_2': float(pl.take_profit_2) if pl.take_profit_2 is not None else None,
            'rr_ratio': pl.rr_ratio or 1.0,
            'calculated_lot': float(pl.calculated_lot or 0.01),
            'risk_amount_usd': float(pl.risk_amount_usd or 0.0),
            'rationale': pl.rationale,
            'status': pl.status,
            'status_display': pl.get_status_display(),
            'created_at': format_vn_time(pl.created_at),
            'updated_at': format_vn_time(pl.updated_at),
        })

    forecasts = []
    forecast_symbols = []
    if current_wallet:
        forecast_symbols = list(current_wallet.allowed_symbols or [])
    if not forecast_symbols:
        forecast_symbols = [s['symbol'] for s in symbols[:6]]
    for sym_name in forecast_symbols:
        fc = MarketForecast.objects.filter(symbol=sym_name).order_by('-updated_at').first()
        if not fc:
            continue
        try:
            ind = fc.indicators if isinstance(fc.indicators, dict) else {}
        except Exception:
            ind = {}
        forecasts.append({
            'symbol': fc.symbol,
            'timeframe': fc.timeframe,
            'trend_bias': fc.trend_bias,
            'trend_bias_display': fc.get_trend_bias_display(),
            'confidence_score': float(fc.confidence_score or 0),
            'execution': entry_preview(current_wallet, SymbolConfig.objects.filter(symbol=fc.symbol).first(), fc),
            'current_price': float(fc.current_price or 0),
            'projected_target_zone': fc.projected_target_zone,
            'next_resistance_1': float(fc.next_resistance_1 or 0),
            'next_resistance_2': float(fc.next_resistance_2 or 0),
            'next_support_1': float(fc.next_support_1 or 0),
            'next_support_2': float(fc.next_support_2 or 0),
            'trigger_condition': fc.trigger_condition,
            'smc_structure': fc.smc_structure,
            'analysis_rationale': fc.analysis_rationale,
            'recommended_action': fc.recommended_action,
            'recommended_action_display': fc.get_recommended_action_display(),
            'indicators': ind,
            'updated_at': format_vn_time(fc.updated_at),
            'updated_at_raw': fc.updated_at.isoformat() if fc.updated_at else '',
        })

    # 5. History — chỉ lệnh đóng trong ngày GMT+7 (DB)
    history = []
    hist_qs = TradeHistory.objects.select_related('wallet').order_by('-closed_at')
    if current_wallet:
        day_start, day_end = local_day_bounds()
        hist_qs = hist_qs.filter(wallet=current_wallet, closed_at__gte=day_start, closed_at__lt=day_end)
    else:
        hist_qs = hist_qs.none()
    for h in hist_qs[:100]:
        h_source = getattr(h, 'source', 'BOT') or 'BOT'
        history.append({
            'id': h.id,
            'ticket': h.ticket,
            'wallet_id': h.wallet_id,
            'wallet_name': h.wallet.name if h.wallet else '',
            'symbol': h.symbol,
            'position_type': h.position_type,
            'lot_size': float(h.lot_size or 0.01),
            'open_price': float(h.open_price or 0.0),
            'close_price': float(h.close_price or 0.0),
            'stop_loss': float(h.stop_loss) if h.stop_loss else None,
            'take_profit': float(h.take_profit) if h.take_profit else None,
            'pnl': float(h.pnl or 0.0),
            'commission': float(h.commission or 0.0),
            'swap': float(h.swap or 0.0),
            'pips': float(h.pips or 0.0),
            'close_reason': h.close_reason,
            'close_reason_display': h.get_close_reason_display(),
            'is_win': h.is_win,
            'source': h_source,
            'source_display': 'BOT (Tự Động)' if h_source == 'BOT' else 'USER (Người Dùng)',
            'comment': getattr(h, 'comment', ''),
            'opened_at': format_vn_time(h.opened_at),
            'closed_at': format_vn_time(h.closed_at),
        })

    bot_m = daily_by_id.get(current_wallet.id) if current_wallet else None
    if not bot_m:
        bot_m = {'bot': _empty_source_metrics(), 'user': _empty_source_metrics()}

    return {
        'overview': {
            'total_balance': float(tot_bal),
            'total_equity': float(tot_eq),
            'total_floating_pnl': float(tot_fl),
            'total_today_pnl': float(tot_td),
            'total_profit': float(tot_prof),
            'overall_winrate': winrate,
            'active_positions_count': len(live_rows) if live_rows else len(positions),
            'active_wallets_count': 1 if current_wallet else 0,
            'total_wallets_count': len(wallets),
            'active_wallet_id': current_wallet.id if current_wallet else None,
            'active_wallet_name': current_wallet.name if current_wallet else '',
            'active_wallet_bot_status': (
                current_wallet.bot_status if current_wallet else None
            ),
            'active_wallet_bot_running': bool(
                current_wallet
                and current_wallet.is_active
                and current_wallet.bot_status == 'RUNNING'
            ),
            'bot_metrics': bot_m['bot'],
            'user_metrics': bot_m['user'],
        },
        'symbols': symbols,
        'positions': positions,
        'wallets': wallets,
        'plans': plans,
        'forecasts': forecasts,
        'history': history,
        'timestamp': format_vn_time(timezone.now(), '%H:%M:%S')
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
    """Báo cáo số liệu ví đang kích hoạt (không cộng tổng nhiều ví)."""
    current = WalletAccount.get_current()
    wallets = WalletAccount.objects.all()
    if not current:
        empty = _empty_source_metrics()
        return Response({
            'total_balance': 0.0,
            'total_equity': 0.0,
            'total_floating_pnl': 0.0,
            'total_today_pnl': 0.0,
            'total_profit': 0.0,
            'total_trades': 0,
            'overall_winrate': 0.0,
            'active_positions_count': 0,
            'active_wallets_count': 0,
            'total_wallets_count': wallets.count(),
            'active_wallet_id': None,
            'active_wallet_name': '',
            'bot_metrics': empty,
            'user_metrics': empty,
            'updated_at': format_vn_time(timezone.now(), '%H:%M:%S %d/%m/%Y')
        })

    mt5_today, mt5_login = _mt5_today_for_wallets([current])
    daily = current.get_daily_breakdown()
    total_today = Decimal(str(daily['all']['today_pnl']))
    if mt5_today.get('ok'):
        # Native chỉ bổ sung PnL hôm nay; số lệnh/winrate vẫn theo ngày GMT+7 trên DB
        daily['bot']['today_pnl'] = float(mt5_today.get('BOT', daily['bot']['today_pnl']) or 0)
        daily['user']['today_pnl'] = float(mt5_today.get('USER', daily['user']['today_pnl']) or 0)
        daily['all']['today_pnl'] = float(mt5_today.get('all', daily['all']['today_pnl']) or 0)
        total_today = Decimal(str(daily['all']['today_pnl']))
    total_trades = current.total_trades
    total_wins = current.winning_trades
    overall_winrate = round((total_wins / total_trades) * 100, 1) if total_trades > 0 else 0.0
    active_positions_count = Position.objects.filter(wallet=current).count()

    bot_stats = daily['bot']
    user_stats = daily['user']

    data = {
        'total_balance': float(current.balance),
        'total_equity': float(current.equity),
        'total_floating_pnl': float(current.floating_pnl),
        'total_today_pnl': float(total_today),
        'total_profit': float(current.total_profit),
        'total_trades': total_trades,
        'overall_winrate': overall_winrate,
        'active_positions_count': active_positions_count,
        'active_wallets_count': 1,
        'total_wallets_count': wallets.count(),
        'active_wallet_id': current.id,
        'active_wallet_name': current.name,
        'bot_metrics': bot_stats,
        'user_metrics': user_stats,
        'updated_at': format_vn_time(timezone.now(), '%H:%M:%S %d/%m/%Y')
    }
    return Response(data)


@api_view(['GET'])
def wallet_list_api(request):
    """Danh sách các ví Exness kèm thông tin tóm tắt."""
    wallets = WalletAccount.objects.all()
    mt5_today, mt5_login = _mt5_today_snap()
    live_acc = None
    try:
        from apps.trading.mt5_session import MT5NativeSession
        if MT5NativeSession.available() and mt5_login:
            live_acc = MT5NativeSession.account()
    except Exception:
        live_acc = None
    result = []
    for w in wallets:
        breakdown = w.get_performance_breakdown()
        today_val = w.get_today_pnl()
        if mt5_today.get('ok') and mt5_login and str(w.mt5_login or '') == mt5_login:
            today_val = Decimal(str(mt5_today['all']))
            breakdown['bot']['today_pnl'] = mt5_today['BOT']
            breakdown['user']['today_pnl'] = mt5_today['USER']
        w_bal, w_eq, w_fl = _live_wallet_money(w, live_acc, mt5_login)
        result.append({
            'id': w.id,
            'name': w.name,
            'account_type': w.account_type,
            'account_type_display': w.get_account_type_display(),
            'mt5_login': w.mt5_login,
            'mt5_server': w.mt5_server,
            'currency': w.currency,
            'leverage': w.leverage,
            'leverage_display': w.leverage_display,
            'capital': float(w.capital),
            'balance': float(w_bal),
            'equity': float(w_eq),
            'floating_pnl': float(w_fl),
            'margin': float(w.margin),
            'margin_free': float(w.margin_free),
            'margin_level': float(w.margin_level),
            'today_pnl': float(today_val),
            'total_profit': float(w.total_profit),
            'win_rate': w.win_rate,
            'total_trades': w.total_trades,
            'active_trades_count': w.positions.count(),
            'default_lot_size': float(w.default_lot_size or 0.01),
            'max_open_trades': int(w.max_open_trades or 1),
            'min_take_profit_usd': _usd_or_none(w.min_take_profit_usd),
            'max_stop_loss_usd': _usd_or_none(getattr(w, 'max_stop_loss_usd', None)),
            'trail_sl_enabled': bool(getattr(w, 'trail_sl_enabled', False)),
            'trail_sl_lock_usd': _usd_or_none(getattr(w, 'trail_sl_lock_usd', None)),
            'is_active': w.is_active,
            'bot_status': w.bot_status,
            'bot_status_display': w.get_bot_status_display(),
            'allowed_symbols': w.allowed_symbols,
            'bot_metrics': breakdown['bot'],
            'user_metrics': breakdown['user'],
        })
    return Response(result)


@api_view(['GET'])
def wallet_detail_api(request, wallet_id):
    """Chi tiết toàn diện của 1 ví: Report riêng, Phân tích dự báo tiếp theo, Plans, Positions, History."""
    try:
        w = WalletAccount.objects.get(pk=wallet_id)
    except WalletAccount.DoesNotExist:
        return Response({'error': 'Không tìm thấy ví'}, status=status.HTTP_404_NOT_FOUND)

    breakdown = w.get_performance_breakdown()
    mt5_today, mt5_login = _mt5_today_snap()
    today_val = w.get_today_pnl()
    if mt5_today.get('ok') and mt5_login and str(w.mt5_login or '') == mt5_login:
        today_val = Decimal(str(mt5_today['all']))
        breakdown['bot']['today_pnl'] = mt5_today['BOT']
        breakdown['user']['today_pnl'] = mt5_today['USER']
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
        'margin': float(w.margin),
        'margin_free': float(w.margin_free),
        'margin_level': float(w.margin_level),
        'leverage': w.leverage,
        'leverage_display': w.leverage_display,
        'today_pnl': float(today_val),
        'total_profit': float(w.total_profit),
        'win_rate': w.win_rate,
        'total_trades': w.total_trades,
        'winning_trades': w.winning_trades,
        'losing_trades': w.losing_trades,
        'profit_factor': w.profit_factor,
        'max_drawdown': w.max_drawdown,
        'max_stop_loss_usd': _usd_or_none(getattr(w, 'max_stop_loss_usd', None)),
        'min_take_profit_usd': _usd_or_none(w.min_take_profit_usd),
        'trail_sl_enabled': bool(getattr(w, 'trail_sl_enabled', False)),
        'trail_sl_lock_usd': _usd_or_none(getattr(w, 'trail_sl_lock_usd', None)),
        'is_active': w.is_active,
        'bot_status': w.bot_status,
        'bot_status_display': w.get_bot_status_display(),
        'allowed_symbols': w.allowed_symbols,
        'performance_breakdown': breakdown,
        'bot_metrics': breakdown['bot'],
        'user_metrics': breakdown['user'],
    }

    # 2. Forward Market Forecasts for the symbols enabled in this wallet
    forecasts_data = []
    for sym_name in w.allowed_symbols:
        forecast = MarketForecast.objects.filter(symbol=sym_name).first()

        if forecast:
            forecasts_data.append({
                'symbol': forecast.symbol,
                'timeframe': forecast.timeframe,
                'trend_bias': forecast.trend_bias,
                'trend_bias_display': forecast.get_trend_bias_display(),
                'confidence_score': forecast.confidence_score,
                'execution': entry_preview(w, SymbolConfig.objects.filter(symbol=forecast.symbol).first(), forecast),
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
                'updated_at': format_vn_time(forecast.updated_at),
            })

    # 3. Trading Plans of this wallet
    plans_data = []
    for p in w.trading_plans.filter(
        status__in=['PENDING_TRIGGER', 'PENDING', 'ANALYZING']
    ).order_by('-updated_at', '-created_at')[:20]:
        plans_data.append({
            'id': p.id,
            'symbol': p.symbol,
            'timeframe': p.timeframe,
            'direction': p.direction,
            'entry_price': float(p.entry_price or 0.0),
            'entry_zone': 'MARKET',
            'stop_loss': float(p.stop_loss) if p.stop_loss is not None else None,
            'take_profit_1': float(p.take_profit_1) if p.take_profit_1 is not None else None,
            'take_profit_2': float(p.take_profit_2) if p.take_profit_2 is not None else None,
            'rr_ratio': p.rr_ratio or 1.0,
            'calculated_lot': float(p.calculated_lot or 0.01),
            'risk_amount_usd': float(p.risk_amount_usd or 0.0),
            'rationale': p.rationale,
            'status': p.status,
            'status_display': p.get_status_display(),
            'created_at': format_vn_time(p.created_at),
        })

    # 4. Active Open Positions of this wallet
    positions_data = []
    for pos in w.positions.all():
        pos_source = getattr(pos, 'source', 'BOT') or 'BOT'
        positions_data.append({
            'id': pos.id,
            'ticket': pos.ticket,
            'symbol': pos.symbol,
            'position_type': pos.position_type,
            'lot_size': float(pos.lot_size or 0.01),
            'open_price': float(pos.open_price or 0.0),
            'current_price': float(pos.current_price or 0.0),
            'stop_loss': float(pos.stop_loss) if pos.stop_loss is not None else None,
            'take_profit': float(pos.take_profit) if pos.take_profit is not None else None,
            'floating_pnl': float(pos.floating_pnl or 0.0),
            'commission': float(pos.commission or 0.0),
            'swap': float(pos.swap or 0.0),
            'net_pnl': float(pos.net_floating_pnl or 0.0),
            'floating_pips': float(pos.floating_pips or 0.0),
            'source': pos_source,
            'source_display': 'BOT (Tự Động)' if pos_source == 'BOT' else 'USER (Người Dùng)',
            'comment': getattr(pos, 'comment', ''),
            'is_trailing': pos.is_trailing,
            'is_breakeven_set': pos.is_breakeven_set,
            'opened_at': format_vn_time(pos.opened_at),
        })

    # 5. Closed Trade History — chỉ trong ngày
    history_data, _raw = _mt5_closed_history_for_wallets([w], limit=200)
    if history_data is None:
        history_data = []
        from apps.core.time_utils import local_day_bounds
        day_start, day_end = local_day_bounds()
        for h in w.trade_history.filter(closed_at__gte=day_start, closed_at__lt=day_end).order_by('-closed_at')[:200]:
            h_source = getattr(h, 'source', 'BOT') or 'BOT'
            history_data.append({
            'id': h.id,
            'ticket': h.ticket,
            'symbol': h.symbol,
            'position_type': h.position_type,
            'lot_size': float(h.lot_size or 0.01),
            'open_price': float(h.open_price or 0.0),
            'close_price': float(h.close_price or 0.0),
            'stop_loss': float(h.stop_loss) if h.stop_loss is not None else None,
            'take_profit': float(h.take_profit) if h.take_profit is not None else None,
            'pnl': float(h.pnl or 0.0),
            'commission': float(h.commission or 0.0),
            'swap': float(h.swap or 0.0),
            'pips': float(h.pips or 0.0),
            'close_reason': h.close_reason,
            'close_reason_display': h.get_close_reason_display(),
            'is_win': h.is_win,
            'source': h_source,
            'source_display': 'BOT (Tự Động)' if h_source == 'BOT' else 'USER (Người Dùng)',
            'comment': getattr(h, 'comment', ''),
            'opened_at': format_vn_time(h.opened_at),
            'closed_at': format_vn_time(h.closed_at),
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
    """Đóng thủ công 1 lệnh đang mở trên MT5. Tra ticket nếu id DB đổi sau lần sync."""
    pos = Position.objects.filter(pk=position_id).first()
    ticket = ''
    if hasattr(request, 'data'):
        ticket = str(request.data.get('ticket') or '').strip()
    if not ticket:
        ticket = str(request.GET.get('ticket') or '').strip()
    if pos is None and ticket:
        pos = Position.objects.filter(ticket=ticket).first()
    if pos is None:
        return Response({'success': False, 'error': 'Không tìm thấy vị thế từ MT5'}, status=status.HTTP_404_NOT_FOUND)
    snap = _snapshot_open_close_result(pos)
    ok, msg = ExecutionEngine.close_position(pos, reason='MANUAL_CLOSE')
    if ok:
        return Response({
            'success': True,
            'message': msg,
            'close': _close_result_for_ticket(snap['ticket'], snap),
        })
    return Response({'success': False, 'error': msg}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def close_all_positions_api(request, wallet_id=None):
    """Đóng tất cả lệnh live trên MT5 của ví đang kích hoạt (hoặc ví chỉ định). Nguồn = USER."""
    wallet = None
    if wallet_id:
        try:
            wallet = WalletAccount.objects.get(pk=wallet_id)
        except WalletAccount.DoesNotExist:
            return Response({'success': False, 'error': 'Không tìm thấy ví'}, status=status.HTTP_404_NOT_FOUND)
    else:
        wallet = WalletAccount.get_current()

    snaps = [
        _snapshot_open_close_result(pos)
        for pos in Position.objects.filter(wallet=wallet)
    ]
    ok, msg, info = ExecutionEngine.close_all_open(wallet)
    closes = [_close_result_for_ticket(s['ticket'], s) for s in snaps]
    net_pnl = round(sum(float(c.get('pnl') or 0) for c in closes), 2) if closes else 0.0
    payload = {
        'success': ok,
        'message': msg,
        'closed': info.get('closed', 0),
        'total': info.get('total', 0),
        'closes': closes,
        'net_pnl': net_pnl,
    }
    if info.get('errors'):
        payload['errors'] = info['errors']
    return Response(payload, status=status.HTTP_200_OK if ok or info.get('closed') else status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def trigger_trading_cycle_api(request):
    """Kích hoạt ngay 1 chu kỳ quét giá & tự động khớp lệnh của Bot."""
    ExecutionEngine.run_full_trading_cycle()
    return Response({'success': True, 'message': 'Đã hoàn tất 1 chu kỳ phân tích & khớp lệnh'})


@api_view(['POST'])
def manual_order_send_api(request):
    """Mở lệnh trực tiếp từ Web lên sàn Exness MT5. Không sync DB nặng sau lệnh (tránh SQLite lock khi đặt liên tục)."""
    from django.db import OperationalError, close_old_connections

    data = request.data
    wallet_id = data.get('wallet_id')
    symbol = str(data.get('symbol', 'XAUUSD')).strip().upper()
    order_type = str(data.get('order_type', 'BUY')).strip().upper()
    try:
        volume = float(data.get('volume', 0.01))
    except (ValueError, TypeError):
        volume = 0.01

    sl = float(data['sl']) if data.get('sl') else 0.0
    tp = float(data['tp']) if data.get('tp') else 0.0
    comment = str(data.get('comment', 'Web Manual Trade'))

    wallet = None
    last_db_err = None
    for attempt in range(5):
        try:
            close_old_connections()
            if wallet_id:
                wallet = WalletAccount.objects.get(pk=wallet_id)
            else:
                wallet = WalletAccount.objects.filter(is_active=True, mt5_login__isnull=False).first()
            last_db_err = None
            break
        except WalletAccount.DoesNotExist:
            return Response({'success': False, 'error': 'Không tìm thấy ví Exness'}, status=status.HTTP_404_NOT_FOUND)
        except OperationalError as e:
            last_db_err = e
            time.sleep(0.05 * (attempt + 1))
    if last_db_err and wallet is None:
        return Response({
            'success': False,
            'error': 'Database đang bận. Thử đặt lại lệnh sau 1 giây.',
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    if not wallet:
        return Response({'success': False, 'error': 'Chưa có ví Exness nào được kích hoạt'}, status=status.HTTP_400_BAD_REQUEST)
    if not wallet.is_active:
        return Response({
            'success': False,
            'error': f"Ví '{wallet.name}' đang tắt kích hoạt — không đặt lệnh / không chạy bot.",
        }, status=status.HTTP_400_BAD_REQUEST)

    from apps.trading.mt5_connector import ExnessMT5Connector
    connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
    if not connector.connect(allow_switch=True):
        return Response({
            'success': False,
            'error': f'Không thể kết nối tới máy chủ MT5 ({wallet.mt5_server}). Hãy mở MetaTrader 5, đăng nhập tài khoản #{wallet.mt5_login} và bật Algo Trading.'
        }, status=status.HTTP_400_BAD_REQUEST)

    res = connector.send_order(
        symbol=symbol,
        order_type=order_type,
        volume=volume,
        sl=sl,
        tp=tp,
        comment=comment,
        magic=0,
    )

    if res and res.get('success'):
        ticket = str(res.get('ticket') or '')
        deal = res.get('deal', '')
        price = res.get('price', 0.0)
        try:
            from apps.trading.order_source import remember_order_source
            remember_order_source(ticket, 'USER', 0)
        except OperationalError:
            pass
        except Exception:
            pass
        BotLog.log(
            level='INFO',
            category='EXECUTION',
            wallet=wallet,
            symbol=symbol,
            message=f"Đã mở lệnh THẬT từ Web lên Exness MT5: {order_type} {volume} Lot {symbol} tại giá {price} (Ticket: #{ticket})"
        )
        return Response({
            'success': True,
            'message': f"Đã gửi lệnh thành công lên Exness MT5! Ticket #{ticket}",
            'ticket': ticket,
            'deal': deal,
            'price': price,
            'volume': volume
        })
    err_msg = res.get('error', 'Lỗi không xác định khi gửi lệnh lên MT5') if res else 'Lỗi kết nối MT5'
    return Response({'success': False, 'error': f"Exness MT5 từ chối lệnh: {err_msg}"}, status=status.HTTP_400_BAD_REQUEST)


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

    # Nếu ví đã tồn tại trong DB, lập tức đồng bộ toàn bộ dữ liệu mới nhất từ MT5 vào DB
    w = None
    if wallet_id:
        try:
            w = WalletAccount.objects.get(pk=wallet_id)
        except WalletAccount.DoesNotExist:
            pass
    elif login:
        w = WalletAccount.objects.filter(mt5_login=login).first()

    if w:
        try:
            if acc_info.get('leverage'):
                w.leverage = int(acc_info['leverage'])
                w.save(update_fields=['leverage'])
            connector = ExnessMT5Connector(login=w.mt5_login, password=w.mt5_password, server=w.mt5_server)
            if connector.connect():
                connector.sync_account_info(w)
                connector.sync_positions(w)
                connector.sync_history_from_mt5(w)
            w.refresh_from_db()
            acc_info['balance'] = float(w.balance)
            acc_info['equity'] = float(w.equity)
            acc_info['margin'] = float(w.margin)
            acc_info['margin_free'] = float(w.margin_free)
            acc_info['leverage'] = w.leverage
            acc_info['leverage_display'] = w.leverage_display
        except Exception:
            pass

    return Response({
        'success': True,
        'message': msg,
        'account_info': acc_info
    })


@api_view(['POST'])
def admin_wallet_bot_toggle_api(request, wallet_id):
    """Bật/tắt bot_status của ví (RUNNING ↔ STOPPED). Giữ is_active và vị thế mở."""
    try:
        wallet = WalletAccount.objects.get(pk=wallet_id)
    except WalletAccount.DoesNotExist:
        return Response({'error': 'Không tìm thấy ví'}, status=status.HTTP_404_NOT_FOUND)

    from apps.trading.mt5_connector import ExnessMT5Connector

    if not wallet.is_active:
        return Response(
            {'error': 'Ví chưa kích hoạt / chưa login MT5 — hãy kích hoạt ví trước khi bật bot.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    data = request.data if hasattr(request, 'data') else {}
    want = str(data.get('bot_status') or '').upper().strip()
    if want not in ('RUNNING', 'STOPPED', 'PAUSED'):
        # Không gửi body → đảo trạng thái
        want = 'STOPPED' if wallet.bot_status == 'RUNNING' else 'RUNNING'

    if want == 'RUNNING':
        login = str(wallet.mt5_login or '').replace('#', '').strip()
        if not login:
            return Response(
                {'error': 'Chưa có số tài khoản MT5 — không thể bật bot.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not ExnessMT5Connector.session_login_matches(wallet):
            return Response(
                {'error': 'Chưa login MT5 tài khoản này. Hãy kích hoạt ví / đăng nhập MT5 trước khi bật bot.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    wallet.bot_status = want
    wallet.save(update_fields=['bot_status'])

    purged = 0
    if want != 'RUNNING':
        purged = AutoPlanGenerator.purge_pending_plans(wallet)
        BotLog.log(
            level='INFO',
            category='SYSTEM',
            message=f"Đã TẮT bot ví '{wallet.name}' ({want}). Xóa {purged} plan chờ. Vị thế mở giữ nguyên.",
            wallet=wallet,
        )
        return Response({
            'success': True,
            'message': f"Đã tắt bot — không mở lệnh mới (đã xóa {purged} plan chờ)." if purged else 'Đã tắt bot — không mở lệnh mới.',
            'wallet_id': wallet.id,
            'bot_status': wallet.bot_status,
            'bot_status_display': wallet.get_bot_status_display(),
            'bot_running': False,
        })

    BotLog.log(
        level='INFO',
        category='SYSTEM',
        message=f"Đã BẬT bot ví '{wallet.name}' (RUNNING). Bot sẽ lập plan khi còn slot.",
        wallet=wallet,
    )
    try:
        ExecutionEngine.try_immediate_market_entries()
    except Exception:
        pass
    return Response({
        'success': True,
        'message': 'Đã bật bot — sẽ vào lệnh theo phân tích khi còn slot.',
        'wallet_id': wallet.id,
        'bot_status': wallet.bot_status,
        'bot_status_display': wallet.get_bot_status_display(),
        'bot_running': True,
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

        want_active = bool(data.get('is_active', True))
        if want_active:
            other = _other_active_wallet()
            if other:
                return Response({'error': _active_wallet_busy_error(other)}, status=status.HTTP_400_BAD_REQUEST)

        if 'Trial' in server_name or 'Demo' in server_name or account_type == 'DEMO':
            detected_type = 'DEMO'
        else:
            detected_type = 'REAL'

        acc_info = {}
        # Chỉ kết nối MT5 khi kích hoạt ví; lưu cấu hình inactive chỉ ghi DB.
        if want_active:
            is_valid, msg, acc_info = ExnessMT5Connector.activate_wallet_session(
                login=mt5_login,
                password=mt5_pass,
                server=server_name,
                account_type=account_type,
            )
            if not is_valid:
                return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

        if want_active and detected_type == 'REAL':
            init_cap = Decimal(str(acc_info.get('balance', 0) or 0))
        elif 'capital' in data and data['capital'] is not None and str(data['capital']).strip() != '':
            init_cap = Decimal(str(data['capital']))
        elif 'balance' in data and data['balance'] is not None and str(data['balance']).strip() != '':
            init_cap = Decimal(str(data['balance']))
        elif want_active:
            init_cap = Decimal(str(acc_info.get('balance', 0) or 0))
        else:
            # Chưa kích hoạt / chưa kết nối MT5 — không giả số dư $1000
            init_cap = Decimal('0.00')

        lev_val = int(acc_info.get('leverage', 2000)) if acc_info and acc_info.get('leverage') else 2000
        wallet = WalletAccount.objects.create(
            name=name,
            account_type=detected_type,
            mt5_login=mt5_login,
            mt5_password=mt5_pass,
            mt5_server=server_name,
            leverage=lev_val,
            capital=init_cap,
            balance_db=init_cap,
            equity_db=init_cap,
            default_lot_size=float(data.get('default_lot_size', 0.01)),
            max_open_trades=max(1, min(500, int(data.get('max_open_trades', 5)))),
            min_take_profit_usd=_optional_usd(data, 'min_take_profit_usd') if 'min_take_profit_usd' in data else None,
            max_stop_loss_usd=_optional_usd(data, 'max_stop_loss_usd') if 'max_stop_loss_usd' in data else None,
            trail_sl_enabled=bool(data.get('trail_sl_enabled', False)),
            trail_sl_lock_usd=_optional_usd(data, 'trail_sl_lock_usd') if 'trail_sl_lock_usd' in data else None,
            is_active=want_active,
            bot_status='STOPPED',
        )
        wallet.set_allowed_symbols(symbols)
        wallet.save()
        from apps.core.trading_defaults import activate_wallet_symbols
        activate_wallet_symbols(symbols)

        if want_active:
            try:
                connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
                if connector.connect(allow_switch=True):
                    connector.sync_account_info(wallet)
                    connector.sync_positions(wallet)
            except Exception:
                pass

            AutoPlanGenerator.refresh_plans_for_wallet(wallet)
            BotLog.log(
                level='INFO',
                category='PLAN',
                message=f"Đã kích hoạt ví '{wallet.name}': login MT5 thành công. Bot đang DỪNG — bấm Bật Bot khi sẵn sàng.",
                wallet=wallet
            )
            payload = {
                'success': True,
                'message': (msg + ' Bot đang dừng — bấm Bật Bot khi sẵn sàng.') if msg else 'Đã kích hoạt ví: login MT5 thành công. Bot đang dừng — bấm Bật Bot khi sẵn sàng.',
                'wallet_id': wallet.id,
            }
            if not acc_info.get('algo_trading'):
                payload.update(_algo_warning_fields())
            else:
                payload['algo_required'] = False
        else:
            payload = {
                'success': True,
                'message': 'Đã lưu cấu hình ví (chưa kích hoạt — không kết nối MT5, không tạo kế hoạch)',
                'wallet_id': wallet.id,
            }
        return Response(payload)

    elif request.method == 'PUT':
        try:
            wallet = WalletAccount.objects.get(pk=wallet_id)
        except WalletAccount.DoesNotExist:
            return Response({'error': 'Không tìm thấy ví'}, status=status.HTTP_404_NOT_FOUND)

        data = request.data
        was_active = bool(wallet.is_active)
        if bool(data.get('is_active', False)) and not wallet.is_active:
            other = _other_active_wallet(exclude_id=wallet.id)
            if other:
                return Response({'error': _active_wallet_busy_error(other)}, status=status.HTTP_400_BAD_REQUEST)

        if 'allowed_symbols' in data:
            symbols = data['allowed_symbols']
            if isinstance(symbols, str):
                symbols = [s.strip() for s in symbols.split(',') if s.strip()]
            if not symbols or len(symbols) == 0:
                return Response({'error': 'Vui lòng chọn ít nhất một cặp giao dịch cho ví'}, status=status.HTTP_400_BAD_REQUEST)
            wallet.set_allowed_symbols(symbols)
            from apps.core.trading_defaults import activate_wallet_symbols
            activate_wallet_symbols(symbols)

        new_server = data.get('mt5_server', wallet.mt5_server).strip()
        new_login = data.get('mt5_login', wallet.mt5_login).strip()
        new_pass = data.get('mt5_password', '').strip() or wallet.mt5_password

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
        if 'default_lot_size' in data: wallet.default_lot_size = float(data['default_lot_size'])
        if 'max_open_trades' in data: wallet.max_open_trades = max(1, min(500, int(data['max_open_trades'])))
        if 'min_take_profit_usd' in data:
            wallet.min_take_profit_usd = _optional_usd(data, 'min_take_profit_usd')
        if 'max_stop_loss_usd' in data:
            wallet.max_stop_loss_usd = _optional_usd(data, 'max_stop_loss_usd')
        if 'trail_sl_enabled' in data:
            wallet.trail_sl_enabled = bool(data.get('trail_sl_enabled'))
        if 'trail_sl_lock_usd' in data:
            wallet.trail_sl_lock_usd = _optional_usd(data, 'trail_sl_lock_usd')
        if 'is_active' in data: wallet.is_active = bool(data['is_active'])
        if not wallet.is_active:
            wallet.bot_status = 'STOPPED'
        elif not was_active:
            # Vừa login / kích hoạt — bot mặc định dừng, user tự bật
            wallet.bot_status = 'STOPPED'

        want_active = bool(wallet.is_active)
        activate_msg = ''
        activate_info = {}

        # Khi kích hoạt: login MT5 (đổi tài khoản nếu cần)
        if want_active:
            is_valid, activate_msg, activate_info = ExnessMT5Connector.activate_wallet_session(
                login=new_login,
                password=new_pass,
                server=new_server,
                account_type=wallet.account_type,
            )
            if not is_valid:
                return Response({'error': activate_msg}, status=status.HTTP_400_BAD_REQUEST)
            if activate_info and activate_info.get('leverage'):
                wallet.leverage = int(activate_info['leverage'])

        wallet.save()

        if want_active:
            try:
                connector = ExnessMT5Connector(login=wallet.mt5_login, password=wallet.mt5_password, server=wallet.mt5_server)
                if connector.connect(allow_switch=True):
                    connector.sync_account_info(wallet)
                    connector.sync_positions(wallet)
            except Exception:
                pass

            AutoPlanGenerator.refresh_plans_for_wallet(wallet)
            BotLog.log(
                level='INFO',
                category='PLAN',
                message=f"Đã làm mới ví '{wallet.name}'. Bot không tự chạy — bấm Bật Bot khi sẵn sàng." if not was_active else f"Đã cập nhật ví '{wallet.name}'.",
                wallet=wallet
            )
            payload = {
                'success': True,
                'message': (
                    (activate_msg or 'Đã login MT5 thành công') + ' Bot đang dừng — bấm Bật Bot khi sẵn sàng.'
                    if not was_active else
                    (activate_msg or 'Cập nhật ví Exness thành công')
                ),
            }
            if not activate_info.get('algo_trading'):
                payload.update(_algo_warning_fields())
            else:
                payload['algo_required'] = False
        else:
            purged = AutoPlanGenerator.purge_all_plans_for_wallet(wallet)
            payload = {
                'success': True,
                'message': (
                    'Đã lưu cấu hình ví (chưa kích hoạt — không kết nối MT5, '
                    f'đã xóa {purged} kế hoạch AI)'
                    if purged else
                    'Đã lưu cấu hình ví (chưa kích hoạt — không kết nối MT5, không tạo kế hoạch)'
                ),
            }
        return Response(payload)

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
                'last_scanned': format_vn_time(s.last_scanned_at, '%H:%M:%S')
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
            'created_at': format_vn_time(l.created_at)
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


@api_view(['GET', 'POST', 'PUT', 'DELETE'])
def admin_servers_api(request, server_id=None):
    """Lấy danh sách / Thêm / Sửa / Xóa Server Exness Master Data."""
    from apps.accounts.models import ExnessServerMaster

    if request.method == 'GET':
        servers = list(ExnessServerMaster.objects.values('id', 'server_name', 'server_type', 'description', 'is_active', 'order'))
        return Response({'success': True, 'servers': servers})

    elif request.method == 'POST':
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
            'created_at': format_vn_time(l.created_at)
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


# ==================== TRADING PLANS APIS ====================

@api_view(['POST', 'DELETE'])
def clear_trading_plans_api(request):
    """Xóa tất cả các kế hoạch giao dịch AI (Trading Plans)."""
    count = TradingPlan.objects.count()
    TradingPlan.objects.all().delete()
    return Response({'success': True, 'message': f'Đã xóa sạch toàn bộ {count} kế hoạch giao dịch AI.'})


@api_view(['POST', 'DELETE'])
def delete_trading_plan_api(request, plan_id):
    """Xóa 1 kế hoạch giao dịch AI cụ thể."""
    try:
        plan = TradingPlan.objects.get(pk=plan_id)
        plan.delete()
        return Response({'success': True, 'message': f'Đã xóa kế hoạch AI #{plan_id} thành công.'})
    except TradingPlan.DoesNotExist:
        return Response({'success': False, 'error': 'Không tìm thấy kế hoạch AI'}, status=status.HTTP_404_NOT_FOUND)


# ==================== PER-WALLET DATA MANAGEMENT ====================

@api_view(['POST', 'DELETE'])
def clear_wallet_plans_api(request, wallet_id):
    """Xóa tất cả các kế hoạch AI của 1 ví cụ thể."""
    try:
        wallet = WalletAccount.objects.get(pk=wallet_id)
        count = wallet.trading_plans.count()
        wallet.trading_plans.all().delete()
        return Response({'success': True, 'message': f"Đã xóa toàn bộ {count} kế hoạch AI của ví '{wallet.name}'!"})
    except WalletAccount.DoesNotExist:
        return Response({'success': False, 'error': 'Không tìm thấy ví Exness'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST', 'DELETE'])
def clear_wallet_history_api(request, wallet_id):
    """Xóa toàn bộ lịch sử lệnh đã đóng của 1 ví cụ thể."""
    try:
        wallet = WalletAccount.objects.get(pk=wallet_id)
        count = wallet.trade_history.count()
        wallet.trade_history.all().delete()
        return Response({'success': True, 'message': f"Đã xóa toàn bộ {count} bản ghi lịch sử lệnh của ví '{wallet.name}'!"})
    except WalletAccount.DoesNotExist:
        return Response({'success': False, 'error': 'Không tìm thấy ví Exness'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
def mt5_status_api(request):
    """Trạng thái MT5 để hiện banner/popup khi terminal chưa bật, chưa login, hoặc tắt Algo Trading."""
    return Response(_mt5_status_with_wallets())


@api_view(['POST'])
def mt5_launch_api(request):
    """Mở (hoặc cài rồi mở) Exness MetaTrader 5 từ web."""
    from apps.trading.mt5_launcher import MT5Launcher
    launched = MT5Launcher.ensure_terminal_running()
    data = MT5Launcher.get_runtime_status(probe_api=True, timeout_ms=2500, use_cache=False)
    data['launched'] = launched
    data['wallet_active'] = WalletAccount.objects.filter(is_active=True).exists()
    data['wallet_running'] = WalletAccount.objects.filter(is_active=True, bot_status='RUNNING').exists()
    return Response(data)







