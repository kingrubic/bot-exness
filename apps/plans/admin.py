from django.contrib import admin
from apps.plans.models import TradingPlan

@admin.register(TradingPlan)
class TradingPlanAdmin(admin.ModelAdmin):
    list_display = ('id', 'wallet', 'symbol', 'direction', 'status', 'entry_price', 'take_profit_1', 'stop_loss', 'calculated_lot', 'created_at')
    list_filter = ('direction', 'status', 'symbol', 'wallet')
    search_fields = ('symbol', 'rationale', 'wallet__name')
    ordering = ('-created_at',)
