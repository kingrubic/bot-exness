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

4. **Trang Quản Trị & Cài Đặt (`/admin-panel/`) — [🔒 YÊU CẦU ĐĂNG NHẬP ADMIN]**:
   - Chỉ Quản trị viên đã đăng nhập mới có quyền truy cập vào đây.
   - **Quản lý Master Data Sàn Exness**:
     - Danh mục **50+ Máy Chủ Exness** (`Exness-MT5Real` 1-35, `Exness-MT5Trial` 1-15) kèm tính năng **Thêm/Xóa Server bằng tay**.
     - Danh mục **Loại Tài Khoản Exness** (Standard, Raw Spread, Zero, Pro, Cent, Demo...) kèm tính năng **Thêm/Xóa Loại tài khoản bằng tay**.
     - Danh mục **Cặp Tiền Master Data** với bộ chọn thẻ chip (Symbol Chips) trực quan.
   - Thêm / Sửa / Xóa ví Exness (Đồng bộ số dư & lệnh 100% realtime từ MT5).
   - Bật / Tắt và tùy chỉnh cặp giao dịch (Khung M1/M5/M15/H1, Chiến thuật SMC/Scalping/Breakout, Max Spread).
   - Cài đặt quản trị rủi ro toàn cục (Risk per trade, Max Daily Drawdown, Trailing Stop).
   - Nút Khẩn cấp: Dừng bot & Đóng toàn bộ lệnh của tất cả các ví.
   - Giám sát nhật ký lỗi kỹ thuật và sự cố của Bot (`BotLog`).

5. **Native MQL5 EA (`mql5/XAUUSD_Exness_Bot.mq5`)**:
   - File mã nguồn EA đầy đủ cho ai muốn chạy trực tiếp trên phần mềm MetaTrader 5 Exness MetaEditor.

---

---

## 🛠️ Hướng Dẫn Cài Đặt & Khởi Động Dự Án

### 1️⃣ Bước 1: Tạo & Kích hoạt Môi Trường Ảo (`venv`)
- **Trên Linux / macOS / VPS**:
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```
- **Trên Windows**:
  ```cmd
  python -m venv venv
  venv\Scripts\activate
  ```

### 2️⃣ Bước 2: Cài đặt các thư viện phụ thuộc
```bash
pip install -r requirements.txt
```

### 3️⃣ Bước 3: Tạo file cấu hình `.env`
```bash
cp .env.example .env
```
Mở file `.env` và kiểm tra/điền thông tin kết nối MySQL của bạn (nếu có mật khẩu):
```env
DJANGO_SECRET_KEY=django-insecure-exness-auto-trade-sandbox-secret-key-2026
DJANGO_DEBUG=True

# Cấu hình MySQL Database
DB_NAME=sandbox_exness
DB_USER=root
DB_PASSWORD=mat_khau_mysql_neu_co
DB_HOST=127.0.0.1
DB_PORT=3306
```

### 4️⃣ Bước 4: Tạo tài khoản Admin Quản Trị
Chạy lệnh tạo tài khoản Admin nhanh qua CMD:
```bash
python3 create_admin.py admin 123123123
```
*(Lệnh này tự động tạo Database `sandbox_exness` trên MySQL và khởi tạo tài khoản: User là `admin`, Password là `123123123`)*

### 5️⃣ Bước 5: Khởi động 1 lệnh (giống Windows `start.bat`)

**Ubuntu / Linux:**
```bash
chmod +x start.sh    # lần đầu
./start.sh
```
Đổi cổng (tùy chọn): `./start.sh 8080`

`./start.sh` tự làm: tạo `venv` nếu thiếu → `pip install` → copy `.env` → tạo admin → **nếu chưa có Wine/MT5 thì tự cài** (apt Wine, Exness MT5, Python trong Wine) → mở MT5 → Wine Bridge cổng `9999` → Web + Bot Worker. Có thể hỏi mật khẩu `sudo` khi cài Wine.

**Windows:** double-click `start.bat` (hoặc `run_bot.bat`).

### 6️⃣ Bước 6: Đăng nhập MT5 rồi kiểm tra Bridge

Sau `./start.sh`, cửa sổ MetaTrader 5 sẽ mở. Đăng nhập tài khoản Exness (login, mật khẩu trading, server). Kiểm tra bridge:
```bash
curl http://127.0.0.1:9999/health
```
Kết quả phải có `"status": "OK"` và `"initialized": true`. Sau đó vào Admin → Ví → **Kiểm Tra Kết Nối Sàn**.

> Lỗi `Chưa tìm thấy kết nối MT5 Terminal đang hoạt động` = bridge `9999` chưa chạy (xem `logs/mt5_wine_bridge.log`).
> MT5 chỉ giữ **1 tài khoản** tại một thời điểm. Ví trên web phải trùng login/server với phiên đang đăng nhập trong MT5.

Chạy tay 3 terminal (nếu không dùng `./start.sh`):

**Terminal 1 — Web + Bot:** `source venv/bin/activate && python3 run_bot.py`  
**Terminal 2 — MT5:** `DISPLAY=:0 WINEPREFIX=~/.mt5 wine "C:\\Program Files\\MetaTrader 5\\terminal64.exe"`  
**Terminal 3 — Bridge:** `DISPLAY=:0 WINEPREFIX=~/.mt5 wine "C:\\Python310\\python.exe" -u "Z:$(pwd)/deploy/mt5_wine_bridge.py"`

---

## 🌐 Đường Dẫn Truy Cập Hệ Thống:

- 🔒 **Đăng Nhập Quản Trị (Admin Login)**: 👉 [http://localhost:8888/login/](http://localhost:8888/login/)
  - Tài khoản mặc định: Username: `admin` | Password: `123123123`
- ⚙️ **Bảng Điều Khiển Quản Trị (Admin Panel)**:
  - 📈 **Tổng Quan & Hiệu Suất Vốn**: 👉 [http://localhost:8888/admin-panel/overview/](http://localhost:8888/admin-panel/overview/)
  - 💼 **Quản Lý Danh Sách Ví & Vào Lệnh**: 👉 [http://localhost:8888/admin-panel/wallets/](http://localhost:8888/admin-panel/wallets/)
  - 🖥️ **Master Data Máy Chủ Exness**: 👉 [http://localhost:8888/admin-panel/master-data/servers/](http://localhost:8888/admin-panel/master-data/servers/)
  - 📊 **Master Data Cặp Giao Dịch**: 👉 [http://localhost:8888/admin-panel/master-data/symbols/](http://localhost:8888/admin-panel/master-data/symbols/)
  - ⚠️ **Nhật Ký & Báo Lỗi Kỹ Thuật (BotLog)**: 👉 [http://localhost:8888/admin-panel/logs/](http://localhost:8888/admin-panel/logs/)

---

## 📁 Cấu Trúc Thư Mục Dự Án:
```
exness/
├── apps/
│   ├── accounts/              # Model Ví, Số dư, Phân loại BOT/USER
│   ├── analysis/              # Technical Analyzer: EMA, RSI, SMC
│   ├── api/                   # REST API & WebSocket Feeds
│   ├── dashboard/             # Views Admin Panel & Auth
│   ├── plans/                 # AutoPlanGenerator: Kế hoạch lướt sóng
│   ├── symbols/               # Cặp tiền & cấu hình spread
│   └── trading/               # Execution Engine & MT5 Connector
├── deploy/
│   └── mt5_wine_bridge.py     # Bridge Server MT5 trên Linux
├── mql5/
│   ├── ClosePosition.mq5      # EA đóng vị thế MT5
│   └── MT5_Command_Bridge.mq5 # EA khớp lệnh MT5
├── templates/
│   ├── admin/                 # Giao diện Quản Trị Hệ Thống
│   └── auth/                  # Giao diện Đăng nhập
├── static/                    # CSS, JS (admin.js) & Webfonts
├── tests/                     # Test Suite (12 unit tests)
├── manage.py
├── start.sh                   # Ubuntu one-click (venv + web + MT5 + Wine Bridge)
├── start.bat                  # Windows one-click
├── run_bot.py
└── README.md
```
