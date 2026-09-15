DEFAULT_ACTIVE_SYMBOLS = ('XAUUSD', 'BTCUSD', 'ETHUSD')
DEFAULT_ALLOWED_SYMBOLS_JSON = '["XAUUSD", "BTCUSD", "ETHUSD"]'
BOT_MAGIC = 8882026


def apply_default_active_symbols():
    """Chỉ bật mặc định XAUUSD, BTCUSD, ETHUSD. Các cặp khác vẫn có trong danh mục nhưng tắt."""
    from apps.symbols.models import SymbolConfig
    from apps.accounts.models import WalletAccount

    SymbolConfig.objects.exclude(symbol__in=DEFAULT_ACTIVE_SYMBOLS).update(is_active=False)
    for symbol in DEFAULT_ACTIVE_SYMBOLS:
        SymbolConfig.objects.filter(symbol=symbol).update(is_active=True)

    for wallet in WalletAccount.objects.all():
        current = set(wallet.allowed_symbols or [])
        if not current or current <= {'XAUUSD'}:
            wallet.allowed_symbols_json = DEFAULT_ALLOWED_SYMBOLS_JSON
            wallet.save(update_fields=['allowed_symbols_json'])
