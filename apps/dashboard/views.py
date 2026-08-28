from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.admin.views.decorators import staff_member_required
from apps.accounts.models import WalletAccount
from apps.symbols.models import SymbolConfig

def home_view(request):
    """Trang chủ Công Khai: Ai cũng có thể vào xem Báo cáo tổng thể lãi lỗ & Danh sách ví/lệnh."""
    wallets = WalletAccount.objects.all()
    symbols = SymbolConfig.objects.filter(is_active=True)
    return render(request, 'dashboard/home.html', {
        'wallets': wallets,
        'symbols': symbols,
    })

def wallet_detail_view(request, wallet_id):
    """Trang chi tiết ví Công Khai: Báo cáo riêng, Dự Báo Nến Tiếp Theo của Bot, Bảng Plans, Bảng Lệnh Mở, Lịch Sử."""
    wallet = get_object_or_404(WalletAccount, pk=wallet_id)
    return render(request, 'dashboard/wallet_detail.html', {
        'wallet': wallet,
    })

# ==================== ADMIN DEDICATED VIEWS (MỖI CHỨC NĂNG 1 LINK) ====================

@staff_member_required(login_url='/login/')
def admin_view(request):
    """Trang Quản Trị: Báo Cáo Hiệu Suất Vốn."""
    wallets = WalletAccount.objects.all()
    symbols = SymbolConfig.objects.filter(is_active=True)
    return render(request, 'admin/overview.html', {
        'wallets': wallets,
        'symbols': symbols,
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
def admin_backtest_view(request):
    """Trang Quản Trị: Kiểm Thử & Backtest Chiến Lược."""
    symbols = SymbolConfig.objects.filter(is_active=True)
    return render(request, 'admin/backtest.html', {
        'symbols': symbols,
        'active_page': 'backtest',
    })

@staff_member_required(login_url='/login/')
def admin_risk_view(request):
    """Trang Quản Trị: Cài Đặt Quản Trị Rủi Ro & Vốn."""
    wallets = WalletAccount.objects.all()
    symbols = SymbolConfig.objects.filter(is_active=True)
    return render(request, 'admin/risk.html', {
        'wallets': wallets,
        'symbols': symbols,
        'active_page': 'risk',
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
    return redirect('home')
