from apps.core.trading_defaults import BOT_MAGIC

# MT5 DEAL_REASON_*
_DEAL_REASON_CLIENT = 0
_DEAL_REASON_MOBILE = 1
_DEAL_REASON_WEB = 2
_DEAL_REASON_EXPERT = 3
_DEAL_REASON_SL = 4
_DEAL_REASON_TP = 5
_DEAL_REASON_SO = 6


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


def remember_close_reason(ticket, reason: str, source: str | None = None, magic: int = 0):
    """Ghi nhận lý do đóng từ app (đóng tay / close-all / bot chốt) để sync MT5 không ghi đè thành TP_HIT."""
    from apps.trading.models import OrderSourceTag
    t = str(ticket or '').strip()
    r = str(reason or '').strip()
    if not t or not r:
        return
    defaults = {'close_reason': r, 'magic': int(magic or 0)}
    if source in ('BOT', 'USER'):
        defaults['source'] = source
    tag, created = OrderSourceTag.objects.get_or_create(ticket=t, defaults={
        'source': source if source in ('BOT', 'USER') else 'USER',
        'magic': int(magic or 0),
        'close_reason': r,
    })
    if not created:
        tag.close_reason = r
        if source in ('BOT', 'USER'):
            tag.source = source
        if magic:
            tag.magic = int(magic)
        tag.save(update_fields=['close_reason', 'source', 'magic', 'updated_at'])


def get_remembered_close_reason(ticket) -> str:
    from apps.trading.models import OrderSourceTag
    t = str(ticket or '').strip()
    if not t:
        return ''
    tag = OrderSourceTag.objects.filter(ticket=t).only('close_reason').first()
    return str(getattr(tag, 'close_reason', '') or '').strip()


def resolve_close_reason(ticket='', deal_reason=None, comment='', source='USER') -> str:
    """Ưu tiên: app đã ghi nhận → MT5 deal.reason → comment đóng tay → heuristic."""
    remembered = get_remembered_close_reason(ticket)
    if remembered:
        return remembered

    try:
        reason_i = int(deal_reason) if deal_reason is not None else -1
    except (TypeError, ValueError):
        reason_i = -1

    if reason_i == _DEAL_REASON_TP:
        return 'TP_HIT'
    if reason_i == _DEAL_REASON_SL:
        return 'SL_HIT'
    if reason_i == _DEAL_REASON_SO:
        return 'STOP_OUT'
    if reason_i in (_DEAL_REASON_CLIENT, _DEAL_REASON_MOBILE, _DEAL_REASON_WEB):
        return 'MANUAL_CLOSE'

    c = str(comment or '').strip().upper()
    if any(k in c for k in ('WEBMANUAL', 'MANUAL', 'CLOSEALL', 'CLOSE ALL')) or c in ('CLOSE', 'WEB CLOSE', 'WEB-CLOSE'):
        return 'MANUAL_CLOSE'

    from apps.trading.models import TradeHistory
    t = str(ticket or '').strip()
    if t:
        hist = TradeHistory.objects.filter(ticket=t).only('close_reason').order_by('-closed_at').first()
        if hist and hist.close_reason:
            return hist.close_reason

    # Không suy BOT = TP_HIT khi đóng qua Expert/API mà không có tag
    if reason_i == _DEAL_REASON_EXPERT:
        return 'MANUAL_CLOSE'
    return 'MANUAL_CLOSE' if source == 'USER' else 'TP_HIT'


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
