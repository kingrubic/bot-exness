# ⚡ EXNESS AUTO-TRADE AI PLATFORM & MULTI-WALLET WEB PORTAL

Hệ thống Bot Auto Trade chuyên sâu cho sàn **Exness** (Vàng XAUUSD, Ngoại hối Forex, Crypto, Dầu thô), được xây dựng trên nền tảng **Django**, cơ sở dữ liệu **MySQL** (`sandbox_exness` cấu hình trong `.env`), tích hợp **Engine AI Tự Động Phân Tích & Dự Báo Bước Giá Kế Tiếp (Forward Market Forecast)** và giao diện Web quản lý đa ví Exness trực quan.

---

## 🌟 Tính Năng Nổi Bật

1. **Quản Lý Đa Ví Exness (Multi-Wallet Management)**:
   - Kết nối nhiều tài khoản Exness đồng thời (Vd: `Ví A - Real`, `Ví B - Demo`, `Ví C - Scalper`).
   - Mỗi ví có **Báo cáo Lãi/Lỗ riêng**, **Bảng lệnh đang mở riêng**, **Bảng lịch sử riêng** và **Cấu hình % rủi ro riêng**.
   - Phân quyền từng cặp giao dịch cho từng ví cụ thể (`XAUUSD`, `EURUSD`, `BTCUSD`, `GBPUSD`, `USOIL`...).

2. **Trang Chủ Báo Cáo & Danh Sách Ví (`/`)**:
   - **Báo cáo Tổng Thể**: Tổng Balance, Tổng Equity, Tổng Floating PnL, Lợi nhuận hôm nay, Tỷ lệ Win Rate tổng.
   - **Danh Sách Các Ví Exness**: Hiển thị thẻ từng ví kèm chỉ số realtime.
   - Nút **"Xem Chi Tiết Ví & Lệnh"** để vào chi tiết từng ví.

3. **Trang Chi Tiết Ví (`/wallet/<id>/`)**:
   - **Báo cáo riêng của ví**: Số dư, Vốn ròng, PnL hôm nay, Win Rate.
   - **🤖 PHẦN PHÂN TÍCH CỦA BOT & DỰ BÁO CÁC BƯỚC GIÁ TIẾP THEO (Forward Market Analysis)**:
     - Xu hướng dự báo kế tiếp (`BULLISH` / `BEARISH` / `SIDEWAY`) kèm điểm tin cậy %.
     - Vùng giá mục tiêu tiếp theo (`Projected Target Zone`).
     - Mức Cản Kế Tiếp (Next R1, R2) & Mức Hỗ Trợ Kế Tiếp (Next S1, S2).
     - Điều kiện kích hoạt lệnh tiếp theo (`Trigger Condition`).
     - Cấu trúc SMC / Price Action & Lý do phân tích chi tiết của Bot.
     - Snapshot chỉ báo kỹ thuật: RSI, EMA 50/200, MACD Histogram, ATR, Spread.
   - **Bảng Kế Hoạch Giao Dịch AI (AI Trading Plans Table)**.
   - **Bảng Vị Thế Đang Mở (Active Positions Table)** kèm nút Đóng Lệnh 1-click.
   - **Bảng Lịch Sử Giao Dịch Đã Đóng (Closed Trades History Table)**.

4. **Trang Quản Trị & Cài Đặt (`/admin-panel/`)**:
   - Thêm / Sửa / Xóa ví Exness (Loại Real/Demo, MT5 ID, Server, % Rủi ro, Cặp cho phép).
   - Bật / Tắt và tùy chỉnh cặp giao dịch (Khung M1/M5/M15/H1, Chiến thuật SMC/Scalping/Breakout, Max Spread).
   - Cài đặt quản trị rủi ro toàn cục (Risk per trade, Max Daily Drawdown, Trailing Stop).
   - Nút Khẩn cấp: Dừng bot & Đóng toàn bộ lệnh của tất cả các ví.

5. **Native MQL5 EA (`mql5/XAUUSD_Exness_Bot.mq5`)**:
   - File mã nguồn EA đầy đủ cho ai muốn chạy trực tiếp trên phần mềm MetaTrader 5 Exness MetaEditor.

---

## 🛠️ Cài Đặt & Cấu Hình

### 1. File Môi Trường `.env`
Hệ thống sử dụng cơ sở dữ liệu `sandbox_exness` được định nghĩa trong file `.env`:
```env
DJANGO_SECRET_KEY=django-insecure-exness-auto-trade-sandbox-secret-key-2026
DJANGO_DEBUG=True

# Cấu hình MySQL Database
DB_NAME=sandbox_exness
DB_USER=root
DB_PASSWORD=
DB_HOST=127.0.0.1
DB_PORT=3306
```

### 2. Cài Đặt Thư Viện
```bash
pip install -r requirements.txt
```

---

## 🔑 Tạo Tài Khoản Admin Qua CMD

Để tạo tài khoản Admin đăng nhập quản trị, chạy lệnh:
```bash
python3 create_admin.py
```
Hoặc tạo trực tiếp nhanh bằng 1 dòng lệnh:
```bash
python3 create_admin.py --username admin --password yourpassword --email admin@example.com
```
Hoặc qua Django CLI:
```bash
python3 manage.py create_admin --username admin --password yourpassword
```

---

## 🚀 Khởi Động Hệ Thống (1-Click)

Chạy lệnh để khởi động toàn bộ Web Dashboard và Bot Worker:
```bash
python3 run_bot.py
```

- **Trang Chủ Theo Dõi**: [http://localhost:8000/](http://localhost:8000/)
- **Trang Quản Trị Admin**: [http://localhost:8000/admin-panel/](http://localhost:8000/admin-panel/)
- **Django Admin**: [http://localhost:8000/admin-django/](http://localhost:8000/admin-django/)

---

## 📁 Cấu Trúc Mã Nguồn

```
exness/
├── .env                       # Cấu hình DB_NAME=sandbox_exness, MySQL configs
├── .env.example               # Mẫu biến môi trường
├── .gitignore                 # Danh sách loại trừ Git
├── PLAN.md                    # Kế hoạch chi tiết & Lộ trình phát triển dự án
├── requirements.txt           # Danh sách thư viện Python
├── run_bot.py                 # Script 1-Click khởi động Web + Bot Engine
├── manage.py                  # Django CLI
├── exness_project/            # Cấu hình Django (settings, urls, wsgi)
├── apps/
│   ├── accounts/              # Model & logic WalletAccount (Real, Demo, Sim)
│   ├── symbols/               # Model SymbolConfig (XAUUSD, EURUSD, BTCUSD...)
│   ├── analysis/              # MarketForecast & TechnicalAnalyzer (Phân tích & Dự báo)
│   ├── plans/                 # TradingPlan & AutoPlanGenerator
│   ├── trading/               # Position, TradeHistory, ExecutionEngine & Risk
│   ├── api/                   # REST API Endpoints
│   └── dashboard/             # Template Views cho Home, Wallet Detail, Admin
├── templates/
│   ├── base.html              # Layout chung, Topbar, Toast notifications
│   ├── dashboard/
│   │   ├── home.html          # Báo cáo tổng thể & Danh sách các ví
│   │   └── wallet_detail.html # Báo cáo riêng + Phân tích Bot tiếp theo + Bảng lệnh
│   └── admin/
│       └── admin_panel.html   # Quản trị kết nối ví, cặp Exness & rủi ro
├── static/
│   ├── css/ (style.css, tables.css)
│   └── js/ (home.js, wallet_detail.js, admin.js)
├── mql5/
│   └── XAUUSD_Exness_Bot.mq5  # Mã nguồn MQL5 Expert Advisor cho MT5
└── tests/
```
