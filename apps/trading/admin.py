from django.contrib import admin
from apps.trading.models import Position, TradeHistory, BotLog, CodeLog

@admin.register(BotLog)
class BotLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'level', 'category', 'wallet', 'symbol', 'message', 'is_resolved', 'created_at')
    list_filter = ('level', 'category', 'is_resolved', 'created_at', 'symbol')
    search_fields = ('message', 'traceback', 'symbol', 'wallet__name')
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)

@admin.register(CodeLog)
class CodeLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'level', 'exception_type', 'module', 'line_number', 'request_method', 'request_path', 'message', 'is_resolved', 'created_at')
    list_filter = ('level', 'exception_type', 'is_resolved', 'created_at')
    search_fields = ('message', 'traceback', 'module', 'exception_type', 'request_path')
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)

@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'wallet', 'symbol', 'position_type', 'lot_size', 'open_price', 'current_price', 'floating_pnl', 'opened_at')
    list_filter = ('position_type', 'symbol', 'wallet')
    search_fields = ('ticket', 'symbol', 'wallet__name')

@admin.register(TradeHistory)
class TradeHistoryAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'wallet', 'symbol', 'position_type', 'lot_size', 'open_price', 'close_price', 'pnl', 'closed_at')
    list_filter = ('position_type', 'symbol', 'wallet')
    search_fields = ('ticket', 'symbol', 'wallet__name')
