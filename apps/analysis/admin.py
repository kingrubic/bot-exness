from django.contrib import admin
from apps.analysis.models import MarketForecast

@admin.register(MarketForecast)
class MarketForecastAdmin(admin.ModelAdmin):
    list_display = ('symbol', 'timeframe', 'trend_bias', 'confidence_score', 'current_price', 'projected_target_zone', 'setup_status', 'recommended_action', 'updated_at')
    list_filter = ('trend_bias', 'setup_status', 'recommended_action', 'timeframe', 'symbol')
    search_fields = ('symbol', 'trigger_condition', 'analysis_rationale', 'smc_structure')
    ordering = ('-updated_at',)
