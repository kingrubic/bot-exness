from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.admin.views.decorators import staff_member_required
from apps.accounts.models import WalletAccount
from apps.symbols.models import SymbolConfig

def home_view(request):
    """Trang chủ Công Khai: Ai cũng có thể vào xem Báo cáo tổng thể lãi lỗ & Danh sách ví/lệnh."""
    from apps.trading.models import Position
    wallets = WalletAccount.objects.all()
    symbols = SymbolConfig.objects.filter(is_active=True)
    tot_bal = sum(w.balance for w in wallets)
    tot_eq = sum(w.equity for w in wallets)
    tot_fl = sum(w.floating_pnl for w in wallets)
    tot_profit = sum(w.total_profit for w in wallets)
    all_positions_count = Position.objects.count()
    return render(request, 'dashboard/home.html', {
        'wallets': wallets,
        'symbols': symbols,
        'tot_bal': tot_bal,
        'tot_eq': tot_eq,
        'tot_fl': tot_fl,
        'tot_profit': tot_profit,
        'all_positions_count': all_positions_count,
    })

def wallet_detail_view(request, wallet_id):
    """Trang chi tiết ví Công Khai: Báo cáo riêng, Dự Báo Nến Tiếp Theo của Bot, Bảng Plans, Bảng Lệnh Mở, Lịch Sử."""
    wallet = get_object_or_404(WalletAccount, pk=wallet_id)
    breakdown = wallet.get_performance_breakdown()
    return render(request, 'dashboard/wallet_detail.html', {
        'wallet': wallet,
        'breakdown': breakdown,
        'bot_metrics': breakdown['bot'],
        'user_metrics': breakdown['user'],
    })

# ==================== ADMIN DEDICATED VIEWS (MỖI CHỨC NĂNG 1 LINK) ====================

@staff_member_required(login_url='/login/')
def admin_view(request):
    """Trang Quản Trị: Báo Cáo Hiệu Suất Vốn."""
    from apps.trading.models import TradeHistory
    from django.db.models import Sum
    from django.utils import timezone

    wallets = WalletAccount.objects.all()
    symbols = SymbolConfig.objects.filter(is_active=True)

    def _calc_stats(qs):
        tot = qs.count()
        wins = qs.filter(pnl__gt=0).count()
        losses = qs.filter(pnl__lt=0).count()
        wr = round((wins / tot) * 100, 1) if tot > 0 else 0.0
        pnl = float(qs.aggregate(s=Sum('pnl'))['s'] or 0.0)
        today = timezone.localdate()
        today_pnl = float(qs.filter(closed_at__date=today).aggregate(s=Sum('pnl'))['s'] or 0.0)
        vol = round(float(qs.aggregate(v=Sum('lot_size'))['v'] or 0.0), 2)
        return {
            'total_trades': tot,
            'winning_trades': wins,
            'losing_trades': losses,
            'win_rate': wr,
            'total_profit': round(pnl, 2),
            'today_pnl': round(today_pnl, 2),
            'total_volume': vol,
        }

    from apps.trading.models import Position
    all_hist = TradeHistory.objects.all()
    bot_metrics = _calc_stats(all_hist.filter(source='BOT'))
    user_metrics = _calc_stats(all_hist.filter(source='USER'))

    tot_bal = sum(w.balance for w in wallets)
    tot_eq = sum(w.equity for w in wallets)
    tot_fl = sum(w.floating_pnl for w in wallets)
    tot_today = sum(w.get_today_pnl() for w in wallets)
    all_positions_count = Position.objects.count()

    return render(request, 'admin/overview.html', {
        'wallets': wallets,
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
    symbols = SymbolConfig.objects.filter(is_active=True)
    return render(request, 'admin/wallets.html', {
        'wallets': wallets,
        'symbols': symbols,
        'active_page': 'wallets',
    })



@staff_member_required(login_url='/login/')
def admin_risk_view(request):
    """Trang Quản Trị: Đã bỏ quản trị rủi ro -> Chuyển tiếp sang Quản lý Ví."""
    return redirect('admin_wallets')

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
    return redirect('home')
