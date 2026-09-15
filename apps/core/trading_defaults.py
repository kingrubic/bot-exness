DEFAULT_ACTIVE_SYMBOLS = ('XAUUSD', 'BTCUSD', 'ETHUSD')
DEFAULT_ALLOWED_SYMBOLS_JSON = '["XAUUSD", "BTCUSD", "ETHUSD"]'
BOT_MAGIC = 8882026


def apply_default_active_symbols():
    """Catalog mặc định XAU/BTC/ETH khi chưa có ví. Không đè cặp đã lưu trên ví."""
    from apps.symbols.models import SymbolConfig
    from apps.accounts.models import WalletAccount

    used = set()
    for wallet in WalletAccount.objects.all():
        used.update(s for s in (wallet.allowed_symbols or []) if s)

    if not used:
        SymbolConfig.objects.exclude(symbol__in=DEFAULT_ACTIVE_SYMBOLS).update(is_active=False)
        for symbol in DEFAULT_ACTIVE_SYMBOLS:
            SymbolConfig.objects.filter(symbol=symbol).update(is_active=True)
        return

    SymbolConfig.objects.filter(symbol__in=used).update(is_active=True)


def activate_wallet_symbols(symbols):
    from apps.symbols.models import SymbolConfig
    names = [str(s).strip() for s in (symbols or []) if str(s).strip()]
    if names:
        SymbolConfig.objects.filter(symbol__in=names).update(is_active=True)
