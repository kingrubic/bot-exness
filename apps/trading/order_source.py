from apps.core.trading_defaults import BOT_MAGIC


def remember_order_source(ticket, source: str, magic: int = 0):
    from apps.trading.models import OrderSourceTag
    t = str(ticket or '').strip()
    if not t:
        return
    src = 'BOT' if source == 'BOT' else 'USER'
    OrderSourceTag.objects.update_or_create(
        ticket=t,
        defaults={'source': src, 'magic': int(magic or 0)},
    )


def classify_order_source(magic=0, comment='', ticket='') -> str:
    from apps.trading.models import OrderSourceTag, Position, TradeHistory
    t = str(ticket or '').strip()
    if t:
        tag = OrderSourceTag.objects.filter(ticket=t).first()
        if tag:
            return tag.source
        pos = Position.objects.filter(ticket=t).only('source').first()
        if pos and pos.source in ('BOT', 'USER'):
            return pos.source
        hist = TradeHistory.objects.filter(ticket=t).only('source').first()
        if hist and hist.source in ('BOT', 'USER'):
            return hist.source
    if int(magic or 0) == BOT_MAGIC:
        return 'BOT'
    c = str(comment or '').strip().upper()
    if c.startswith('BOT') or c.startswith('AI-') or c.startswith('AI '):
        return 'BOT'
    return 'USER'
