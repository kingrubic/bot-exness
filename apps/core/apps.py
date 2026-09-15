from django.apps import AppConfig
from django.db.backends.signals import connection_created

def configure_sqlite(sender, connection, **kwargs):
    """Tự động kích hoạt chế độ WAL (Write-Ahead Logging) và busy_timeout 60s cho SQLite."""
    if connection.vendor == 'sqlite':
        try:
            cursor = connection.cursor()
            cursor.execute('PRAGMA journal_mode = WAL;')
            cursor.execute('PRAGMA synchronous = NORMAL;')
            cursor.execute('PRAGMA busy_timeout = 30000;')
        except Exception:
            pass

class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'

    def ready(self):
        connection_created.connect(configure_sqlite)
