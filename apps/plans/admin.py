from django.contrib import admin
from apps.plans.models import TradingPlan, TradeSignalClaim

@admin.register(TradingPlan)
class TradingPlanAdmin(admin.ModelAdmin):
    list_display = ('id', 'wallet', 'symbol', 'direction', 'status', 'entry_price', 'take_profit_1', 'stop_loss', 'calculated_lot', 'created_at')
    list_filter = ('direction', 'status', 'symbol', 'wallet')
    search_fields = ('symbol', 'rationale', 'wallet__name')
    ordering = ('-created_at',)


@admin.register(TradeSignalClaim)
class TradeSignalClaimAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'wallet', 'symbol', 'direction', 'candle_time',
        'status', 'order_ticket', 'created_at',
    )
    list_filter = ('status', 'direction', 'symbol', 'wallet')
    search_fields = ('symbol', 'wallet__name', 'order_ticket')
    ordering = ('-created_at',)
    readonly_fields = (
        'wallet', 'symbol', 'direction', 'candle_time', 'status',
        'order_ticket', 'error', 'created_at', 'updated_at',
    )
