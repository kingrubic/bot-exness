from django.db import OperationalError
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from apps.trading.models import CodeLog

class CodeExceptionMiddleware(MiddlewareMixin):
    """Bắt exception chưa xử lý: API trả JSON, không trả HTML debug. Không ghi CodeLog khi DB đang lock."""

    def process_exception(self, request, exception):
        is_locked = isinstance(exception, OperationalError) or 'locked' in str(exception).lower()
        if not is_locked:
            try:
                CodeLog.log_exception(
                    exception=exception,
                    request=request,
                    level='ERROR',
                    custom_message=f"Lỗi mã nguồn chưa xử lý: {str(exception)}"
                )
            except Exception as e:
                print(f"[MIDDLEWARE LOG ERROR] Không thể lưu CodeLog: {e}")

        path = getattr(request, 'path', '') or ''
        if path.startswith('/api/'):
            if is_locked:
                msg = 'Database đang bận. Lệnh trên MT5 có thể đã khớp — kiểm tra bảng vị thế rồi thử lại nếu chưa thấy.'
            else:
                msg = str(exception)[:220] or 'Lỗi máy chủ'
            return JsonResponse({'success': False, 'error': msg}, status=503)
        return None
