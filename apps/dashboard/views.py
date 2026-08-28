from django.shortcuts import render, get_object_or_404
from apps.accounts.models import WalletAccount
from apps.symbols.models import SymbolConfig
from apps.analysis.models import MarketForecast

def home_view(request):
    """Trang chủ: Báo cáo tổng thể lãi lỗ & Danh sách tất cả các ví Exness kết nối."""
    wallets = WalletAccount.objects.all()
    symbols = SymbolConfig.objects.filter(is_active=True)
    return render(request, 'dashboard/home.html', {
        'wallets': wallets,
        'symbols': symbols,
    })

def wallet_detail_view(request, wallet_id):
    """Trang chi tiết ví: Báo cáo riêng, Phần Phân Tích Của Bot Tiếp Theo, Bảng Plans, Bảng Lệnh Mở, Lịch Sử."""
    wallet = get_object_or_404(WalletAccount, pk=wallet_id)
    return render(request, 'dashboard/wallet_detail.html', {
        'wallet': wallet,
    })

def admin_view(request):
    """Trang Quản trị: Kết nối ví Exness (Real/Demo), Cấu hình cặp giao dịch, Cài đặt rủi ro, Kill switch."""
    wallets = WalletAccount.objects.all()
    symbols = SymbolConfig.objects.all()
    return render(request, 'admin/admin_panel.html', {
        'wallets': wallets,
        'symbols': symbols,
    })
