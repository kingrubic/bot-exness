import traceback
from django.utils.deprecation import MiddlewareMixin
from apps.trading.models import CodeLog

class CodeExceptionMiddleware(MiddlewareMixin):
    """Middleware tự động bắt mọi ngoại lệ code/backend chưa được try-catch và lưu vào bảng CodeLog trong database."""
    
    def process_exception(self, request, exception):
        try:
            CodeLog.log_exception(
                exception=exception,
                request=request,
                level='ERROR',
                custom_message=f"Lỗi mã nguồn chưa xử lý: {str(exception)}"
            )
        except Exception as e:
            print(f"[MIDDLEWARE LOG ERROR] Không thể lưu CodeLog: {e}")
        return None
