"""Tham số phân tích đa khung.

Mọi ngưỡng của "Kế Hoạch Lệnh Tiếp Theo" nằm ở đây để không rải magic number
trong analyzer. Giá trị chỉ là hệ số/ngưỡng — không có mức giá cứng nào.
"""

# Khung phụ luôn phân tích kèm khung vào lệnh của symbol.
CONTEXT_TIMEFRAMES = ('M15', 'H1', 'H4')
# Khung macro chỉ hiển thị tham khảo, không cộng/trừ điểm vào lệnh.
OPTIONAL_TIMEFRAME = 'D1'

# TTL cache nến theo khung (giây). Nến đã đóng không đổi trong suốt chu kỳ nến,
# nên cache ngắn là đủ để vòng lặp bot 4s không gọi MT5 cho từng khung.
RATES_CACHE_TTL = {
    'M1': 3.0, 'M5': 10.0, 'M15': 20.0, 'M30': 30.0,
    'H1': 45.0, 'H4': 90.0, 'D1': 180.0,
}
RATES_COUNT = 260

# Số nến tối thiểu cho từng nhóm chỉ báo. Thiếu EMA200 thì bỏ thành phần đó ra
# khỏi điểm và chuẩn hoá lại — không coi là tăng hay giảm.
MIN_CANDLES_STRUCTURE = 30
MIN_CANDLES_EMA50 = 50
MIN_CANDLES_EMA200 = 200

# Xác định swing bằng fractal: cần bấy nhiêu nến thấp/cao hơn ở mỗi bên.
SWING_STRENGTH = 2
SWING_LOOKBACK = 60
# Số swing gần nhất đưa vào gom vùng S/R cho mỗi khung.
SWING_ZONE_LIMIT = 6
# Trend chạy mượt có thể không tạo đủ fractal swing; khi đó chia cửa sổ thành
# bấy nhiêu đoạn và lấy cực trị mỗi đoạn làm mức cấu trúc thay thế.
EXTREME_SEGMENTS = 4

# Các mức cách nhau <= tolerance × ATR được gom thành một vùng.
ZONE_ATR_TOLERANCE = 0.35
# Swing của khung lớn tạo vùng nặng ký hơn khi gom.
ZONE_TF_WEIGHTS = {'M1': 0.6, 'M5': 0.8, 'M15': 1.2, 'M30': 1.4, 'H1': 1.8, 'H4': 2.4, 'D1': 3.0}
# Số nến tra ngược để biết vùng có vừa bị phá hay không (phục vụ xác nhận retest).
RETEST_LOOKBACK = 12
# Nến phải đóng vượt biên vùng thêm buffer × ATR mới tính là breakout thật.
BREAKOUT_BUFFER = 0.10
# Breakout candle phải có displacement thật, thân đủ lớn và không bị râu phía
# phá vỡ từ chối quá mạnh. Volume chưa dùng vì feed MT5 hiện chưa chuẩn hoá nó.
BREAKOUT_MIN_BODY_ATR = 0.10
BREAKOUT_MIN_BODY_RATIO = 0.45
BREAKOUT_MAX_REJECTION_WICK_RATIO = 0.35
# Giá cách vùng cản gần nhất dưới ngần này × ATR là quá sát, không vào lệnh.
MIN_ROOM_ATR = 0.60
# Giá đi xa vùng phá vỡ quá ngần này × ATR là đã overextended.
MAX_EXTENSION_ATR = 2.0
# Độ phẳng EMA: |ΔEMA20| dưới ngần này × ATR coi là đi ngang.
FLAT_SLOPE_ATR = 0.25
EMA_SLOPE_LOOKBACK = 5

# Trọng số điểm đa khung, tổng 100.
SCORE_WEIGHTS = {
    'H4': 30.0,
    'H1': 25.0,
    'M15': 12.0,
    'ENTRY': 8.0,
    'BREAKOUT': 15.0,
    'MOMENTUM': 10.0,
}
# Khung đi ngang không loại setup nhưng chỉ được một phần trọng số.
SIDEWAYS_SCORE_FRACTION = 0.40
# Breakout mới thủng bằng râu nến (chưa close) chỉ được một phần trọng số.
WICK_ONLY_SCORE_FRACTION = 0.45

# Trừ điểm khi các khung mâu thuẫn. H4 nặng nhất.
CONFLICT_PENALTY = {
    ('H4', 'H1'): 15.0,
    ('H4', 'ENTRY'): 10.0,
    ('H1', 'ENTRY'): 8.0,
}
# Ngưỡng trạng thái setup.
WATCHING_CONFIDENCE = 50.0
MIN_CONFIDENCE = 75.0

# Kế hoạch entry/SL/TP cấu trúc; execution recheck rồi gửi SL/TP2 lên sàn.
ATR_SL_BUFFER = 0.30
MIN_RISK_REWARD = 1.5
TP2_RR_FALLBACK = 2.5
# Giá đang ở biên cửa sổ quan sát nên không còn vùng cản phía trước: dùng mục
# tiêu đo theo ATR thật thay vì bịa một mức giá.
MEASURED_MOVE_ATR = 2.0

# Bắt buộc nến đã đóng phải phá vùng cản mới cho READY.
REQUIRE_BREAKOUT_CLOSE = True
# Nến tín hiệu cũ hơn ngần này lần chu kỳ nến là dữ liệu chết.
MAX_CANDLE_AGE_FACTOR = 2.0
