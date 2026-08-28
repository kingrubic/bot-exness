from django.urls import path
from apps.api import views

urlpatterns = [
    # Global overview & Wallets
    path('overview/', views.global_overview_api, name='api_global_overview'),
    path('wallets/', views.wallet_list_api, name='api_wallet_list'),
    path('wallets/<int:wallet_id>/', views.wallet_detail_api, name='api_wallet_detail'),
    
    # Trading Actions
    path('positions/<int:position_id>/close/', views.close_position_api, name='api_close_position'),
    path('positions/close-all/', views.close_all_positions_api, name='api_close_all_positions'),
    path('positions/close-all/<int:wallet_id>/', views.close_all_positions_api, name='api_close_wallet_positions'),
    path('bot/trigger-cycle/', views.trigger_trading_cycle_api, name='api_trigger_trading_cycle'),
    
    # Admin APIs
    path('admin/wallets/', views.admin_wallet_manage_api, name='api_admin_create_wallet'),
    path('admin/wallets/<int:wallet_id>/', views.admin_wallet_manage_api, name='api_admin_manage_wallet'),
    path('admin/symbols/', views.admin_symbols_api, name='api_admin_symbols'),
    path('admin/symbols/<int:symbol_id>/', views.admin_symbols_api, name='api_admin_manage_symbol'),
    path('admin/logs/', views.admin_logs_api, name='api_admin_logs'),
    path('admin/logs/<int:log_id>/resolve/', views.admin_log_resolve_api, name='api_admin_log_resolve'),
    path('admin/logs/clear/', views.admin_logs_clear_api, name='api_admin_logs_clear'),
    path('backtest/run/', views.run_backtest_api, name='api_run_backtest'),
]
