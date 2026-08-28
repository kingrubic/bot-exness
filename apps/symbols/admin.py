from django.contrib import admin
from apps.symbols.models import SymbolConfig

@admin.register(SymbolConfig)
class SymbolConfigAdmin(admin.ModelAdmin):
    list_display = ('symbol', 'category', 'timeframe', 'strategy', 'max_allowed_spread', 'is_active', 'last_scanned_at')
    list_filter = ('category', 'timeframe', 'strategy', 'is_active')
    search_fields = ('symbol',)
