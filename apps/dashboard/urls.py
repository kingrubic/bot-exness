from django.urls import path
from django.shortcuts import redirect
from apps.dashboard import views

urlpatterns = [
    # Gốc chuyển hướng trực tiếp vào Quản Trị Admin Panel
    path('', lambda req: redirect('admin_panel'), name='home'),
    path('wallet/<int:wallet_id>/', lambda req, wallet_id: redirect('admin_wallets'), name='wallet_detail'),
    path('wallets/<int:wallet_id>/', lambda req, wallet_id: redirect('admin_wallets'), name='wallets_detail_alias'),
    
    # Admin Panel Dedicated Routes (Chuyên biệt Quản Trị Hệ Thống)
    path('admin-panel/', views.admin_view, name='admin_panel'),
    path('admin-panel/overview/', views.admin_view, name='admin_overview'),
    path('admin-panel/wallets/', views.admin_wallets_view, name='admin_wallets'),
    path('admin-panel/master-data/servers/', views.admin_master_servers_view, name='admin_master_servers'),
    path('admin-panel/master-data/symbols/', views.admin_master_symbols_view, name='admin_master_symbols'),
    path('admin-panel/logs/', views.admin_logs_view, name='admin_logs'),

    # Authentication
    path('login/', views.login_view, name='login_view'),
    path('logout/', views.logout_view, name='logout_view'),
]
