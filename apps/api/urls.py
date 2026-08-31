from django.urls import path
from apps.api import views

urlpatterns = [
    # Realtime Live Streaming Ticker (SSE & REST Fallback)
    path('live-ticks/', views.live_ticks_api, name='api_live_ticks'),
    path('stream-ticks/', views.stream_ticks_api, name='api_stream_ticks'),
    
    # Global overview & Wallets
    path('overview/', views.global_overview_api, name='api_global_overview'),
    path('wallets/', views.wallet_list_api, name='api_wallet_list'),
    path('wallets/<int:wallet_id>/', views.wallet_detail_api, name='api_wallet_detail'),
    path('wallets/<int:wallet_id>/clear-plans/', views.clear_wallet_plans_api, name='api_clear_wallet_plans'),
    path('wallets/<int:wallet_id>/clear-history/', views.clear_wallet_history_api, name='api_clear_wallet_history'),
    
    # Trading Actions & Plans
    path('orders/send/', views.manual_order_send_api, name='api_order_send'),
    path('positions/<int:position_id>/close/', views.close_position_api, name='api_close_position'),
    path('positions/close-all/', views.close_all_positions_api, name='api_close_all_positions'),
    path('positions/close-all/<int:wallet_id>/', views.close_all_positions_api, name='api_close_wallet_positions'),
    path('bot/trigger-cycle/', views.trigger_trading_cycle_api, name='api_trigger_trading_cycle'),
    path('plans/clear/', views.clear_trading_plans_api, name='api_clear_trading_plans'),
    path('plans/<int:plan_id>/delete/', views.delete_trading_plan_api, name='api_delete_trading_plan'),
    
    # Admin APIs
    path('admin/wallets/test-connection/', views.admin_wallet_test_connection_api, name='api_admin_wallet_test_connection'),
    path('admin/wallets/', views.admin_wallet_manage_api, name='api_admin_create_wallet'),
    path('admin/wallets/<int:wallet_id>/', views.admin_wallet_manage_api, name='api_admin_manage_wallet'),
    path('admin/symbols/', views.admin_symbols_api, name='api_admin_symbols'),
    path('admin/symbols/<int:symbol_id>/', views.admin_symbols_api, name='api_admin_manage_symbol'),
    path('admin/logs/', views.admin_logs_api, name='api_admin_logs'),
    path('admin/logs/<int:log_id>/resolve/', views.admin_log_resolve_api, name='api_admin_log_resolve'),
    path('admin/logs/clear/', views.admin_logs_clear_api, name='api_admin_logs_clear'),
    path('admin/master-data/', views.admin_master_data_api, name='api_admin_master_data'),
    path('admin/servers/', views.admin_servers_api, name='api_admin_servers'),
    path('admin/servers/<int:server_id>/', views.admin_servers_api, name='api_admin_manage_server'),
    path('admin/change-password/', views.admin_change_password_api, name='api_admin_change_password'),
    path('admin/logs/code/', views.admin_code_logs_api, name='api_admin_code_logs'),
    path('admin/logs/code/<int:log_id>/resolve/', views.admin_code_log_resolve_api, name='api_admin_code_log_resolve'),
    path('admin/logs/code/clear/', views.admin_code_logs_clear_api, name='api_admin_code_logs_clear'),
]
