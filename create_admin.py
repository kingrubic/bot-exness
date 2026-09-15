#!/usr/bin/env python
"""
Script tạo tài khoản Admin Superuser cho hệ thống qua CMD / Terminal.
Chỉ yêu cầu: Username + Password.

Cách dùng:
    python3 create_admin.py
    hoặc:
    python3 create_admin.py admin 123456
    hoặc:
    python3 create_admin.py --username admin --password 123456
"""
import os
import sys
import argparse
from pathlib import Path

# Add packages and workspace to sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR / 'packages'))
sys.path.insert(0, str(BASE_DIR))

import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'exness_project.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.core.management import execute_from_command_line

def create_admin(username, password):
    try:
        # Ensure migrations are applied
        execute_from_command_line(['manage.py', 'migrate', '--verbosity', '0'])
        
        from apps.accounts.models import ExnessServerMaster
        if ExnessServerMaster.objects.count() == 0:
            from apps.core.seed_data import run_seed
            run_seed()

        User = get_user_model()
        email = f"{username}@admin.local"
        
        if User.objects.filter(username=username).exists():
            user = User.objects.get(username=username)
            user.set_password(password)
            user.is_staff = True
            user.is_superuser = True
            user.save()
            print(f"\n🔄 Tài khoản admin '{username}' đã tồn tại -> Đã cập nhật mật khẩu mới thành công!")
        else:
            User.objects.create_superuser(
                username=username,
                email=email,
                password=password
            )
            print(f"\n✅ Tạo tài khoản Admin Superuser thành công!")
            print(f"   - Tên đăng nhập (Username): {username}")
            print(f"   - Mật khẩu (Password):      {password}")

        print("\n🌐 Đăng nhập vào trang quản trị:")
        print("   👉 http://localhost:8888/login/\n")
    except Exception as e:
        print(f"\n⚠️ [LỖI KẾT NỐI DATABASE]: {e}")
        print("👉 Vui lòng đảm bảo dịch vụ MySQL đang chạy và kiểm tra thông tin trong file .env!\n")

def main():
    # Support positional arguments: python3 create_admin.py admin admin123
    username = None
    password = None

    if len(sys.argv) == 3 and not sys.argv[1].startswith('-'):
        username = sys.argv[1]
        password = sys.argv[2]
    else:
        parser = argparse.ArgumentParser(description="Tạo tài khoản Admin Superuser")
        parser.add_argument('--username', '-u', type=str, default=None, help='Tên đăng nhập admin')
        parser.add_argument('--password', '-p', type=str, default=None, help='Mật khẩu admin')
        args, _ = parser.parse_known_args()
        username = args.username
        password = args.password

    # Interactive prompt if not supplied via command line
    if not username:
        username = input("Nhập Tên đăng nhập (mặc định 'admin'): ").strip() or "admin"
        
    if not password:
        import getpass
        try:
            password = getpass.getpass("Nhập Mật khẩu (mặc định 'admin123'): ").strip() or "admin123"
        except Exception:
            password = input("Nhập Mật khẩu (mặc định 'admin123'): ").strip() or "admin123"

    create_admin(username, password)

if __name__ == '__main__':
    main()
