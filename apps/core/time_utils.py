from datetime import datetime, time, timedelta
from django.utils import timezone


def local_day_bounds(day=None):
    """[00:00, 24:00) theo TIME_ZONE (Asia/Ho_Chi_Minh) — không dùng UTC date."""
    day = day or timezone.localdate()
    start = datetime.combine(day, time.min, tzinfo=timezone.get_current_timezone())
    return start, start + timedelta(days=1)


def today_closed_q(day=None):
    """Q object lọc lệnh đóng trong ngày local (VN)."""
    from django.db.models import Q
    start, end = local_day_bounds(day)
    return Q(closed_at__gte=start, closed_at__lt=end)


def format_vn_time(dt, fmt='%H:%M:%S %d/%m/%Y'):
    """Định dạng thời gian chuẩn múi giờ Việt Nam (Asia/Ho_Chi_Minh GMT+7)."""
    if not dt:
        return ''
    try:
        return timezone.localtime(dt).strftime(fmt)
    except Exception:
        try:
            return dt.strftime(fmt)
        except Exception:
            return str(dt)
