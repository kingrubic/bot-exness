from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.admin.views.decorators import staff_member_required
from apps.accounts.models import WalletAccount
from apps.symbols.models import SymbolConfig

# ==================== ADMIN DEDICATED VIEWS ====================

@staff_member_required(login_url='/login/')
def admin_view(request):
    """Trang Quản Trị: Báo Cáo Hiệu Suất Vốn & Tổng Quan (SSR nhẹ — số liệu realtime qua /api/live-ticks/)."""
    from apps.trading.models import Position

    wallets = WalletAccount.objects.all()
    current_wallet = WalletAccount.get_current()
    symbols = SymbolConfig.objects.filter(is_active=True)

    empty = {
        'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
        'win_rate': 0.0, 'total_profit': 0.0, 'today_pnl': 0.0, 'total_volume': 0.0,
    }
    tot_today = tot_bal = tot_eq = tot_fl = 0
    all_positions_count = 0
    bot_metrics = dict(empty)
    user_metrics = dict(empty)

    if current_wallet:
        tot_bal = current_wallet.balance
        tot_eq = current_wallet.equity
        tot_fl = current_wallet.floating_pnl
        tot_today = current_wallet.get_today_pnl()
        all_positions_count = Position.objects.filter(wallet=current_wallet).count()
        daily = current_wallet.get_daily_breakdown()
        bot_metrics = daily['bot']
        user_metrics = daily['user']
        tot_today = daily['all']['today_pnl']

    return render(request, 'admin/overview.html', {
        'wallets': wallets,
        'current_wallet': current_wallet,
        'symbols': symbols,
        'bot_metrics': bot_metrics,
        'user_metrics': user_metrics,
        'tot_bal': tot_bal,
        'tot_eq': tot_eq,
        'tot_fl': tot_fl,
        'tot_today': tot_today,
        'all_positions_count': all_positions_count,
        'active_page': 'overview',
    })

@staff_member_required(login_url='/login/')
def admin_wallets_view(request):
    """Trang Quản Trị: Quản Lý Danh Sách Ví (Real & Demo)."""
    wallets = WalletAccount.objects.all()
    symbols = SymbolConfig.objects.all().order_by('category', 'symbol')
    return render(request, 'admin/wallets.html', {
        'wallets': wallets,
        'symbols': symbols,
        'active_page': 'wallets',
    })

@staff_member_required(login_url='/login/')
def admin_master_servers_view(request):
    """Trang Quản Trị: Master Data Máy Chủ Sàn Exness."""
    return render(request, 'admin/master_servers.html', {
        'active_page': 'master_servers',
    })

@staff_member_required(login_url='/login/')
def admin_master_symbols_view(request):
    """Trang Quản Trị: Master Data Danh Mục Cặp Giao Dịch."""
    symbols = SymbolConfig.objects.all()
    return render(request, 'admin/master_symbols.html', {
        'symbols': symbols,
        'active_page': 'master_symbols',
    })

@staff_member_required(login_url='/login/')
def admin_logs_view(request):
    """Trang Quản Trị: Nhật Ký & Báo Lỗi Bot."""
    return render(request, 'admin/logs.html', {
        'active_page': 'logs',
    })

# ==================== AUTH VIEWS ====================

def login_view(request):
    """Trang đăng nhập dành cho Quản trị viên."""
    if request.user.is_authenticated and request.user.is_staff:
        return redirect('admin_panel')
        
    error = None
    if request.method == 'POST':
        u = request.POST.get('username', '').strip()
        p = request.POST.get('password', '').strip()
        next_url = request.POST.get('next', '/admin-panel/')
        
        user = authenticate(request, username=u, password=p)
        if user is not None and user.is_staff:
            login(request, user)
            return redirect(next_url)
        else:
            error = 'Tên đăng nhập hoặc mật khẩu không đúng!'
            
    return render(request, 'auth/login.html', {
        'error': error,
        'next': request.GET.get('next', '/admin-panel/')
    })

def logout_view(request):
    """Đăng xuất tài khoản Admin."""
    logout(request)
    return redirect('login_view')
