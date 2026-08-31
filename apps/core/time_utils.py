from django.utils import timezone

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
