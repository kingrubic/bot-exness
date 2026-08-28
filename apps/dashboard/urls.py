from django.urls import path
from apps.dashboard import views

urlpatterns = [
    # Public Pages
    path('', views.home_view, name='home'),
    path('wallet/<int:wallet_id>/', views.wallet_detail_view, name='wallet_detail'),
    
    # Admin Panel Dedicated Routes (Từng chức năng 1 link riêng biệt)
    path('admin-panel/', views.admin_view, name='admin_panel'),
    path('admin-panel/overview/', views.admin_view, name='admin_overview'),
    path('admin-panel/wallets/', views.admin_wallets_view, name='admin_wallets'),
    path('admin-panel/backtest/', views.admin_backtest_view, name='admin_backtest'),
    path('admin-panel/risk/', views.admin_risk_view, name='admin_risk'),
    path('admin-panel/master-data/servers/', views.admin_master_servers_view, name='admin_master_servers'),
    path('admin-panel/master-data/symbols/', views.admin_master_symbols_view, name='admin_master_symbols'),
    path('admin-panel/logs/', views.admin_logs_view, name='admin_logs'),

    # Auth
    path('login/', views.login_view, name='login_view'),
    path('logout/', views.logout_view, name='logout_view'),
]
