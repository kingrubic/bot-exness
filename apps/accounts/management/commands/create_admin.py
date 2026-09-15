from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = 'Tạo tài khoản Admin Superuser (Chỉ cần Username + Password)'

    def add_arguments(self, parser):
        parser.add_argument('username', nargs='?', default='admin', help='Tên đăng nhập')
        parser.add_argument('password', nargs='?', default='123123123', help='Mật khẩu')

    def handle(self, *args, **options):
        username = options['username']
        password = options['password']
        email = f"{username}@admin.local"
        
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            user = User.objects.get(username=username)
            user.set_password(password)
            user.is_staff = True
            user.is_superuser = True
            user.save()
            self.stdout.write(self.style.SUCCESS(f"🔄 Tài khoản '{username}' đã tồn tại -> Đã cập nhật mật khẩu mới!"))
        else:
            User.objects.create_superuser(username=username, email=email, password=password)
            self.stdout.write(self.style.SUCCESS(f"✅ Đã tạo thành công tài khoản Admin '{username}'!"))
