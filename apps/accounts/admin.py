from django.contrib import admin
from apps.accounts.models import WalletAccount, ExnessServerMaster

@admin.register(WalletAccount)
class WalletAccountAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'account_type', 'mt5_login', 'mt5_server', 'balance', 'equity', 'floating_pnl', 'today_pnl', 'win_rate', 'bot_status', 'is_active')
    list_filter = ('account_type', 'bot_status', 'is_active')
    search_fields = ('name', 'mt5_login', 'mt5_server')

@admin.register(ExnessServerMaster)
class ExnessServerMasterAdmin(admin.ModelAdmin):
    list_display = ('server_name', 'server_type', 'description', 'is_active', 'order')
    list_filter = ('server_type', 'is_active')
    search_fields = ('server_name', 'description')
    ordering = ('order', 'server_name')
