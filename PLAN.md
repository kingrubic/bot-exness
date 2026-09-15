# 📋 KẾ HOẠCH PHÁT TRIỂN DỰ ÁN: EXNESS AUTO-TRADE AI PLATFORM

> **Tài liệu Kế hoạch Tổng thể & Lộ trình Phát triển (Master Development Plan & Roadmap)**  
> **Dự án**: Bot Auto Trade Exness & Web Multi-Wallet Portal  
> **Framework**: Python Django 5.x & Django REST Framework  
> **Cơ sở dữ liệu**: MySQL 100% (`sandbox_exness` cấu hình trong `.env`)  
> **Phiên bản hiện tại**: v2.1.0 (Production-Ready)

---

## 1. TỔNG QUAN KIẾN TRÚC ĐÃ HOÀN THÀNH

```
exness/
├── .env                       # Cấu hình DB_NAME=sandbox_exness, Host, Port
├── .env.example               # Mẫu biến môi trường
├── .gitignore                 # Danh sách loại trừ Git (Bảo mật .env, venv, cache, db)
├── PLAN.md                    # File kế hoạch tổng thể dự án (Master Plan)
├── README.md                  # Hướng dẫn sử dụng chi tiết
├── requirements.txt           # Danh sách thư viện Python
├── run_bot.py                 # Script 1-Click khởi động toàn bộ Web + Bot Worker
├── manage.py                  # Django CLI Management
├── exness_project/            # Cấu hình Django Project (settings, urls, wsgi, asgi)
├── apps/
│   ├── accounts/              # Quản lý Đa Ví Exness (WalletAccount: Real, Demo, Sim)
│   ├── symbols/               # Cấu hình Cặp Giao Dịch (SymbolConfig: XAUUSD, EURUSD, BTCUSD...)
│   ├── analysis/              # MarketForecast & TechnicalAnalyzer (Phân tích & Dự báo kế tiếp)
│   ├── plans/                 # TradingPlan & AutoPlanGenerator (Lập kế hoạch AI)
│   ├── trading/               # Position, TradeHistory, ExecutionEngine & MT5 Connector
│   ├── strategies/            # Thư viện chiến thuật (SMC, Gold Scalper, Breakout, News Filter)
│   ├── backtest/              # Backtesting Engine & Đo lường định lượng (Sharpe, Drawdown)
│   ├── api/                   # REST API Endpoints phục vụ Frontend
│   └── dashboard/             # Views render giao diện Web
├── templates/
│   ├── base.html              # Layout Topbar, Status Pill, Toast alerts
│   ├── dashboard/
│   │   ├── home.html          # Trang Chủ: Báo cáo tổng thể & Danh sách các ví Exness
│   │   └── wallet_detail.html # Chi tiết ví: Báo cáo riêng + Phân tích Bot tiếp theo + Bảng lệnh
│   └── admin/
│       └── admin_panel.html   # Quản trị Admin: Kết nối ví, cài đặt cặp & quản trị rủi ro
├── static/
│   ├── css/ (style.css, tables.css)
│   └── js/ (home.js, wallet_detail.js, admin.js)
├── mql5/
│   └── XAUUSD_Exness_Bot.mq5  # Native EA cho MetaTrader 5 MetaEditor
└── tests/
    └── test_all.py            # Unit test toàn diện (100% OK)
```

---

## 2. CHI TIẾT CÁC PHÂN HỆ ĐÃ XÂY DỰNG

### 2.1 Quản Lý Đa Ví Exness (Multi-Wallet Engine)
- Mỗi ví hoạt động hoàn toàn độc lập (`REAL`, `DEMO`, `SIMULATION`).
- Báo cáo số liệu riêng: Balance, Equity, Floating PnL, Today PnL, Win Rate %, Profit Factor, Max Drawdown %.
- Quản trị rủi ro riêng theo từng ví: % Rủi ro/lệnh (`risk_percent`), số lệnh mở tối đa (`max_open_trades`), danh sách cặp cho phép chạy (`allowed_symbols`).

### 2.2 Engine Phân Tích Kỹ Thuật & Dự Báo Bước Giá Kế Tiếp (Forward Market Forecast)
- Tự động quét dữ liệu nến đa khung thời gian (H1 xu hướng chính + M15 điểm vào).
- Dự báo chi tiết:
  - **Xu hướng kế tiếp**: `BULLISH` (Tăng mạnh), `BEARISH` (Giảm mạnh), `SIDEWAY` kèm điểm tin cậy %.
  - **Vùng Giá Mục Tiêu Tiếp Theo (Projected Target Zone)**.
  - **Mức Cản Kế Tiếp (Next R1, R2)** & **Mức Hỗ Trợ Kế Tiếp (Next S1, S2)**.
  - **Điều Kiện Kích Hoạt Lệnh Tiếp Theo (Trigger Condition)**.
  - **Cấu trúc SMC / Price Action & Lý do phân tích kỹ thuật của Bot**.
  - **Snapshot chỉ báo**: RSI (14), EMA 50/200, MACD Histogram, ATR, Spread pips.

### 2.3 Engine Tự Động Lên Kế Hoạch & Khớp Lệnh (AI Plan & Execution Engine)
- Tự động tạo `TradingPlan` khi thị trường có tín hiệu đạt chuẩn.
- Tính toán khối lượng Lot an toàn theo công thức:
  $$\text{Lot Size} = \frac{\text{Balance} \times \text{Risk \%}}{\text{SL Distance} \times \text{Contract Size}}$$
- Kích hoạt lệnh sang `Position`, tự động dời Stop Loss về hòa vốn (Break-Even) khi đạt mục tiêu R:R và kích hoạt Trailing Stop theo ATR.
- Tự động đóng lệnh khi chạm Take Profit (TP) hoặc Stop Loss (SL) và lưu trữ vào `TradeHistory`.

### 2.4 Thư Viện Chiến Thuật Chuyên Sâu (Multi-Strategy Library)
- **`SMCOrderBlockStrategy`**: Cấu trúc thị trường Cung Cầu, Liquidity Sweep, Fair Value Gap (FVG), Order Block.
- **`GoldScalperStrategy`**: Scalping nhanh Vàng M1/M5 theo dải Bollinger Bands + RSI Quá mua/Quá bán.
- **`SessionBreakoutStrategy`**: Đột phá đỉnh/đáy phiên Á khi mở phiên London / New York.
- **`EconomicNewsFilter`**: Bộ lọc tin tức kinh tế đỏ (NFP, CPI, FOMC) bảo vệ tài khoản khỏi giãn Spread.

### 2.5 Giao Diện Web 3 Tầng Trực Quan
- **Trang Chủ (`/`)**: Báo cáo tổng thể toàn bộ tài khoản + Lưới các thẻ ví Exness kết nối + Nút vào chi tiết từng ví.
- **Trang Chi Tiết Ví (`/wallet/<id>/`)**: Báo cáo riêng của ví + Thẻ phân tích dự báo tiếp theo của Bot + Bảng AI Plans + Bảng Vị thế mở (nút đóng lệnh nhanh) + Bảng Lịch sử + Nút Xuất Báo Cáo CSV.
- **Trang Quản Trị Admin (`/admin-panel/`)**: Thêm/Sửa/Xóa ví Exness, Bật/Tắt đa cặp giao dịch, Cài đặt rủi ro toàn cục, **Trung tâm Giám Sát & Báo Cáo Lỗi Bot (`BotLog` & Error Monitor)** hiển thị trực tiếp chi tiết lỗi kỹ thuật (Traceback) và trạng thái xử lý sự cố.

---

## 3. LỘ TRÌNH PHÁT TRIỂN TIẾP THEO (FUTURE ROADMAP)

Dựa trên nền tảng vững chắc hiện tại, các giai đoạn nâng cấp tiếp theo có thể tiếp tục triển khai:

```
[Hiện Tại: v2.1.0] ──► [Pha 1: AI / LLM Reasoning] ──► [Pha 2: Realtime WebSockets] ──► [Pha 3: Copy-Trade] ──► [Pha 4: TradingView Webhook]
```

### 🔹 Pha 1: Tích hợp Trí Tuệ Nhân Tạo (AI / LLM Signal Reasoner)
- **Mục tiêu**: Bổ sung bộ phân tích ngữ nghĩa thị trường sử dụng mô hình AI (OpenAI / DeepSeek / Claude / Local LLM).
- **Tính năng**:
  - Đọc tin tức vĩ mô hàng ngày và chấm điểm tâm lý thị trường (Market Sentiment Score).
  - Kết hợp phân tích kỹ thuật của bot với nhận định AI để tăng độ chính xác của `confidence_score` lên trên 90%.
  - Tự động viết báo cáo tổng kết thị trường hàng ngày gửi về Web Dashboard.

### 🔹 Pha 2: Nâng cấp Realtime WebSockets (Django Channels + Redis)
- **Mục tiêu**: Đẩy tick giá và Floating PnL từng lệnh theo thời gian thực (Microsecond) mà không cần Polling.
- **Tính năng**:
  - Tích hợp `channels` và `channels_redis`.
  - Biểu đồ nến nhảy realtime trực tiếp trên trình duyệt khi có giá tick mới từ MT5.

### 🔹 Pha 3: Hệ Thống Copy-Trade Đa Ví (Multi-Wallet Copy Trading)
- **Mục tiêu**: Tự động nhân bản lệnh từ Ví Master sang nhiều Ví Con theo tỷ lệ vốn.
- **Tính năng**:
  - Chọn 1 ví làm **Ví Master (Leader)**.
  - Các ví khác đăng ký làm **Ví Follower (Sao chép)**.
  - Khi Bot mở lệnh trên Ví Master, hệ thống tự động tính tỷ lệ vốn và mở lệnh tương ứng trên tất cả các ví Follower.

### 🔹 Pha 4: Tích hợp Webhook TradingView & PineScript
- **Mục tiêu**: Cho phép nhận tín hiệu từ các Indicator / Script trên TradingView để kích hoạt lệnh trên Exness.
- **Tính năng**:
  - Endpoint `POST /api/webhook/tradingview/`.
  - Xác thực bảo mật qua Secret Token.
  - Tự động map symbol TradingView (`OANDA:XAUUSD`, `BINANCE:BTCUSDT`) sang symbol Exness (`XAUUSDm`, `BTCUSD`).

### 🔹 Pha 5: Ứng Dụng Di Động PWA (Progressive Web App)
- **Mục tiêu**: Cài đặt ứng dụng trực tiếp lên điện thoại iOS / Android như app native.
- **Tính năng**:
  - Service Worker hỗ trợ offline caching.
  - Web Push Notifications thông báo khi có lệnh mới hoặc khi chạm TP/SL.

---

## 4. HƯỚNG DẪN THAO TÁC & VẬN HÀNH DỰ ÁN (TỪ A ĐẾN Z)

### 4.1 Quy Trình Khởi Tạo & Chạy Dự Án
```bash
# 1. Tạo và kích hoạt môi trường ảo (venv)
python3 -m venv venv
source venv/bin/activate    # (Trên Windows: venv\Scripts\activate)

# 2. Cài đặt thư viện
pip install -r requirements.txt

# 3. Tạo file .env từ file mẫu và điền mật khẩu MySQL nếu có
cp .env.example .env

# 4. Tạo tài khoản Admin (tự động tạo DB sandbox_exness trên MySQL)
python3 create_admin.py admin 123123123

# 5. Khởi động Web Dashboard và Bot Worker
python3 run_bot.py
```

### 4.2 Chạy Test Suite Tự Động
```bash
python3 manage.py test tests
```

### 4.3 Chạy Lệnh Quét & Khớp Lệnh Thủ Công Qua API
```bash
curl -X POST http://localhost:8000/api/bot/trigger-cycle/
```

### 4.4 Chạy Backtest Qua API
```bash
curl -X POST http://localhost:8000/api/backtest/run/ \
  -H "Content-Type: application/json" \
  -d '{"symbol": "XAUUSD", "strategy": "SMC_TREND", "initial_balance": 10000, "days": 30}'
```

---

## 5. BẢNG PHÂN CÔNG TẬP TIN & CHỨC NĂNG

| Đường Dẫn Tệp | Chức Năng Chính |
|---|---|
| [`.env`](file:///home/ubuntu/Documents/resource/github-user/exness/.env) | Cấu hình máy chủ và cơ sở dữ liệu `sandbox_exness` |
| [`create_admin.py`](file:///home/ubuntu/Documents/resource/github-user/exness/create_admin.py) | Script tạo tài khoản Admin Superuser qua dòng lệnh CMD |
| [`run_bot.py`](file:///home/ubuntu/Documents/resource/github-user/exness/run_bot.py) | Khởi chạy 1-click toàn bộ Web Dashboard & Bot Background Worker |
| [`apps/accounts/models.py`](file:///home/ubuntu/Documents/resource/github-user/exness/apps/accounts/models.py) | Model `WalletAccount` quản lý các ví Real, Demo, Sim |
| [`apps/symbols/models.py`](file:///home/ubuntu/Documents/resource/github-user/exness/apps/symbols/models.py) | Model `SymbolConfig` quản lý cấu hình các cặp Exness |
| [`apps/analysis/models.py`](file:///home/ubuntu/Documents/resource/github-user/exness/apps/analysis/models.py) | Model `MarketForecast` lưu trữ dự báo bước giá tiếp theo |
| [`apps/analysis/analyzer.py`](file:///home/ubuntu/Documents/resource/github-user/exness/apps/analysis/analyzer.py) | Engine phân tích kỹ thuật đa khung & sinh dự báo thị trường |
| [`apps/plans/planner.py`](file:///home/ubuntu/Documents/resource/github-user/exness/apps/plans/planner.py) | Tự động sinh `TradingPlan` với tỷ lệ R:R chuẩn & tính Lot an toàn |
| [`apps/trading/execution_engine.py`](file:///home/ubuntu/Documents/resource/github-user/exness/apps/trading/execution_engine.py) | Vòng lặp khớp lệnh, trailing stop, break-even và tính toán PnL |
| [`apps/trading/mt5_connector.py`](file:///home/ubuntu/Documents/resource/github-user/exness/apps/trading/mt5_connector.py) | Bridge kết nối trực tiếp với MetaTrader 5 của sàn Exness |
| [`apps/strategies/`](file:///home/ubuntu/Documents/resource/github-user/exness/apps/strategies/) | Thư viện 4 chiến thuật: SMC Order Block, Gold Scalper, Breakout, News Filter |
| [`apps/backtest/engine.py`](file:///home/ubuntu/Documents/resource/github-user/exness/apps/backtest/engine.py) | Engine Backtesting dữ liệu lịch sử |
| [`templates/dashboard/home.html`](file:///home/ubuntu/Documents/resource/github-user/exness/templates/dashboard/home.html) | Giao diện Trang Chủ theo dõi lãi lỗ tổng thể & danh sách ví |
| [`templates/dashboard/wallet_detail.html`](file:///home/ubuntu/Documents/resource/github-user/exness/templates/dashboard/wallet_detail.html) | Giao diện Chi Tiết Ví: Phân tích Bot tiếp theo + Bảng lệnh + Xuất CSV |
| [`templates/admin/admin_panel.html`](file:///home/ubuntu/Documents/resource/github-user/exness/templates/admin/admin_panel.html) | Giao diện Quản Trị Admin: Kết nối ví, cài đặt cặp & rủi ro |
| [`mql5/XAUUSD_Exness_Bot.mq5`](file:///home/ubuntu/Documents/resource/github-user/exness/mql5/XAUUSD_Exness_Bot.mq5) | Mã nguồn Expert Advisor cho MetaTrader 5 |
| [`tests/test_all.py`](file:///home/ubuntu/Documents/resource/github-user/exness/tests/test_all.py) | Bộ Unit Test tự động kiểm thử toàn bộ hệ thống |
