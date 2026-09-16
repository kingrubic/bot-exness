"""
MT5 WINE BRIDGE SERVER
Chạy bên trong môi trường Wine Windows Python (C:\\Python310\\python.exe).
Giao tiếp trực tiếp với MetaTrader 5 Terminal và cung cấp REST API cho Django Linux.
"""
import os
import sys
import time
import json
import logging
from flask import Flask, request, jsonify

# Thiết lập logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("MT5_Bridge")

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logger.error("MetaTrader5 package not available in this Python environment!")

app = Flask(__name__)

TERMINAL_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
is_initialized = False

def ensure_mt5_init(login=None, password=None, server=None):
    global is_initialized
    if not MT5_AVAILABLE:
        return False, "MetaTrader5 library not installed."
    
    # 1. Try quick initialize with running terminal
    try:
        if mt5.initialize():
            is_initialized = True
            return True, "Initialized"
    except Exception:
        pass

    # 2. Try with explicit path and params
    init_kwargs = {}
    if os.path.exists(TERMINAL_PATH):
        init_kwargs['path'] = TERMINAL_PATH
    if login and password and server:
        try:
            init_kwargs['login'] = int(login)
            init_kwargs['password'] = str(password)
            init_kwargs['server'] = str(server)
            init_kwargs['timeout'] = 25000
        except Exception:
            pass
        
    try:
        res = mt5.initialize(**init_kwargs)
        if res:
            is_initialized = True
            logger.info("✅ MetaTrader 5 Terminal initialized successfully.")
            return True, "Initialized"
        else:
            err = mt5.last_error()
            return False, f"Initialize failed: {err}"
    except Exception as e:
        return False, f"Exception initializing MT5: {e}"

@app.route('/health', methods=['GET'])
def health():
    ok, msg = ensure_mt5_init()
    term_info = None
    if ok:
        info = mt5.terminal_info()
        if info:
            term_info = info._asdict()
    return jsonify({
        'status': 'OK' if ok else 'STANDBY',
        'mt5_available': MT5_AVAILABLE,
        'initialized': is_initialized,
        'message': msg,
        'terminal_info': term_info
    })

@app.route('/login', methods=['POST'])
def login_account():
    data = request.get_json(force=True)
    login_id = int(str(data.get('login', 0)).replace('#', '').strip())
    password = str(data.get('password', '')).strip()
    server = str(data.get('server', '')).strip()

    if not login_id or not password or not server:
        return jsonify({'success': False, 'error': 'Vui lòng cung cấp đầy đủ login, password, server'}), 400

    # 0. Check if already logged into requested account
    acc = None
    try:
        acc = mt5.account_info()
    except Exception:
        pass

    if acc and acc.login == login_id:
        logger.info(f"✅ Tài khoản Exness MT5 #{acc.login} đã đăng nhập sẵn ({acc.server}) - Balance: ${acc.balance}")
        return jsonify({
            'success': True,
            'message': f"Kết nối sàn Exness thành công #{acc.login} ({acc.server}) - Số dư thực tế: ${acc.balance:,.2f}",
            'account_info': {
                'login': acc.login,
                'server': acc.server,
                'name': acc.name,
                'balance': float(acc.balance),
                'equity': float(acc.equity),
                'profit': float(acc.profit),
                'margin': float(acc.margin),
                'margin_free': float(acc.margin_free),
                'margin_level': float(acc.margin_level) if acc.margin_level else 0.0,
                'leverage': acc.leverage,
                'currency': acc.currency,
                'trade_allowed': acc.trade_allowed,
                'mode': 'EXNESS_LIVE_MT5_WINE'
            }
        })

    logger.info(f"Đang đăng nhập tài khoản Exness #{login_id} trên server '{server}'...")
    
    # 1. Initialize directly with account credentials
    init_ok = False
    try:
        init_ok = mt5.initialize(path=TERMINAL_PATH, login=login_id, password=password, server=server, timeout=25000)
    except Exception as ie:
        logger.warning(f"mt5.initialize with creds error: {ie}")

    if not init_ok:
        try:
            mt5.initialize(path=TERMINAL_PATH)
            mt5.login(login=login_id, password=password, server=server, timeout=25000)
        except Exception:
            pass

    acc = None
    try:
        acc = mt5.account_info()
    except Exception:
        pass

    if acc and acc.login == login_id:
        logger.info(f"✅ Đăng nhập Exness MT5 thành công #{acc.login} ({acc.server}) - Balance: ${acc.balance}")
        return jsonify({
            'success': True,
            'message': f"Kết nối sàn Exness thành công #{acc.login} ({acc.server}) - Số dư thực tế: ${acc.balance:,.2f}",
            'account_info': {
                'login': acc.login,
                'server': acc.server,
                'name': acc.name,
                'balance': float(acc.balance),
                'equity': float(acc.equity),
                'profit': float(acc.profit),
                'margin': float(acc.margin),
                'margin_free': float(acc.margin_free),
                'margin_level': float(acc.margin_level) if acc.margin_level else 0.0,
                'leverage': acc.leverage,
                'currency': acc.currency,
                'trade_allowed': acc.trade_allowed,
                'mode': 'EXNESS_LIVE_MT5_WINE'
            }
        })
    
    err = mt5.last_error()
    logger.warning(f"❌ Đăng nhập Exness MT5 thất bại #{login_id}: {err}")
    return jsonify({
        'success': False,
        'error': f"Đăng nhập Exness thất bại: Mã lỗi MT5 {err}"
    }), 400

@app.route('/account_info', methods=['GET', 'POST'])
def get_account_info():
    ok, msg = ensure_mt5_init()
    if not ok:
        return jsonify({'success': False, 'error': msg}), 400

    data = request.get_json(silent=True) or {}
    login_id = request.args.get('login') or data.get('login')
    password = request.args.get('password') or data.get('password')
    server = request.args.get('server') or data.get('server')

    if login_id:
        try:
            login_int = int(login_id)
            acc = mt5.account_info()
            if not acc or acc.login != login_int:
                logger.info(
                    "Bỏ qua tự login #%s trên /account_info — chỉ POST /login mới đổi tài khoản.",
                    login_int,
                )
        except Exception:
            pass

    acc = mt5.account_info()
    if acc:
        return jsonify({
            'success': True,
            'account_info': {
                'login': acc.login,
                'server': acc.server,
                'name': acc.name,
                'balance': float(acc.balance),
                'equity': float(acc.equity),
                'profit': float(acc.profit),
                'margin': float(acc.margin),
                'margin_free': float(acc.margin_free),
                'margin_level': float(acc.margin_level) if acc.margin_level else 0.0,
                'leverage': acc.leverage,
                'currency': acc.currency,
                'trade_allowed': acc.trade_allowed,
                'mode': 'EXNESS_LIVE_MT5_WINE'
            }
        })
    return jsonify({'success': False, 'error': 'Chưa đăng nhập tài khoản MT5'}), 400

@app.route('/symbol_price', methods=['POST'])
def get_symbol_price():
    ok, msg = ensure_mt5_init()
    if not ok:
        return jsonify({'success': False, 'error': msg}), 400

    data = request.get_json(force=True)
    symbol = str(data.get('symbol', '')).strip()
    
    # Select symbol candidates into Market Watch
    candidates = [symbol, f"{symbol}m", f"{symbol}_i", f"{symbol}c", symbol.upper()]
    tick = None
    sym_info = None
    for sym_name in candidates:
        try:
            mt5.symbol_select(sym_name, True)
            tick = mt5.symbol_info_tick(sym_name)
            if tick and (tick.bid > 0 or tick.ask > 0):
                sym_info = mt5.symbol_info(sym_name)
                break
        except Exception:
            pass

    if tick:
        point = sym_info.point if sym_info else 0.01
        spread_pips = round((tick.ask - tick.bid) / (point * 10), 1) if point > 0 else 0.0
        return jsonify({
            'success': True,
            'price': {
                'symbol': symbol,
                'bid': float(tick.bid),
                'ask': float(tick.ask),
                'last': float(tick.last),
                'spread_pips': spread_pips,
                'time': tick.time
            }
        })
    return jsonify({'success': False, 'error': f'Không tìm thấy giá cho mã {symbol}'}), 404

@app.route('/all_prices', methods=['GET', 'POST'])
def get_all_prices():
    ok, msg = ensure_mt5_init()
    if not ok:
        return jsonify({'success': False, 'error': msg}), 400

    popular = ['XAUUSDm', 'BTCUSDm', 'ETHUSDm', 'EURUSDm', 'GBPUSDm', 'USDJPYm', 'USDCADm', 'AUDUSDm', 'US30m', 'XAUUSD', 'BTCUSD', 'EURUSD', 'GBPUSD', 'USDJPY']
    for p_sym in popular:
        try:
            mt5.symbol_select(p_sym, True)
        except Exception:
            pass

    symbols_data = {}
    all_syms = mt5.symbols_get()
    if all_syms:
        for s in all_syms:
            if s.select:
                tick = mt5.symbol_info_tick(s.name)
                if tick and (tick.bid > 0 or tick.ask > 0):
                    point = s.point if s.point > 0 else 0.01
                    spread_pips = round((tick.ask - tick.bid) / (point * 10), 1)
                    clean_name = s.name.rstrip('m_ic')
                    item = {
                        'symbol': s.name,
                        'clean_symbol': clean_name,
                        'bid': float(tick.bid),
                        'ask': float(tick.ask),
                        'last': float(tick.last or tick.bid),
                        'spread_pips': spread_pips,
                        'time': tick.time
                    }
                    symbols_data[s.name] = item
                    symbols_data[clean_name] = item
    return jsonify({'success': True, 'prices': symbols_data})

@app.route('/positions', methods=['GET', 'POST'])
def get_positions():
    ok, msg = ensure_mt5_init()
    if not ok:
        return jsonify({'success': False, 'error': msg}), 400

    data = request.get_json(silent=True) or {}
    login_id = request.args.get('login') or data.get('login')
    password = request.args.get('password') or data.get('password')
    server = request.args.get('server') or data.get('server')

    if login_id:
        try:
            login_int = int(login_id)
            acc = mt5.account_info()
            if not acc or acc.login != login_int:
                logger.info(
                    "Bỏ qua tự login #%s trên /positions — giữ phiên MT5 hiện tại.",
                    login_int,
                )
        except Exception:
            pass

    live_positions = mt5.positions_get()
    result = []
    if live_positions:
        for pos in live_positions:
            pos_type = 'BUY' if pos.type == mt5.POSITION_TYPE_BUY else 'SELL'
            magic = int(getattr(pos, 'magic', 0) or 0)
            comment = str(getattr(pos, 'comment', '') or '').strip()
            is_bot = (magic == 8882026) or comment.upper().startswith('BOT') or comment.upper().startswith('AI') or any(k in comment.lower() for k in ['ai', 'bot', 'autobot', 'scalp', 'bot_auto', 'ai_scalp'])
            source = 'BOT' if is_bot else 'USER'
            result.append({
                'ticket': str(pos.ticket),
                'symbol': pos.symbol,
                'position_type': pos_type,
                'lot_size': float(pos.volume),
                'open_price': float(pos.price_open),
                'current_price': float(pos.price_current),
                'stop_loss': float(pos.sl) if pos.sl > 0 else None,
                'take_profit': float(pos.tp) if pos.tp > 0 else None,
                'profit': float(pos.profit),
                'commission': float(getattr(pos, 'commission', 0.0) or 0.0),
                'swap': float(getattr(pos, 'swap', 0.0) or 0.0),
                'time': pos.time,
                'magic': magic,
                'comment': comment,
                'source': source
            })
    return jsonify({'success': True, 'positions': result})

@app.route('/history_deals', methods=['GET', 'POST'])
def get_history_deals():
    ok, msg = ensure_mt5_init()
    if not ok:
        return jsonify({'success': False, 'error': msg}), 400

    # Chuyển đổi tài khoản nếu có truyền thông tin login
    data = request.get_json(silent=True) or {}
    login_id = request.args.get('login') or data.get('login')
    password = request.args.get('password') or data.get('password')
    server = request.args.get('server') or data.get('server')

    if login_id:
        try:
            login_int = int(login_id)
            acc = mt5.account_info()
            if not acc or acc.login != login_int:
                logger.info(
                    "Bỏ qua tự login #%s khi lấy lịch sử — không đổi tài khoản MT5.",
                    login_int,
                )
        except Exception as le:
            logger.warning(f"Lỗi đọc phiên MT5 khi lấy lịch sử: {le}")

    from datetime import datetime as dt_now, timedelta
    now = dt_now.now()
    date_from = now - timedelta(days=90)
    deals = mt5.history_deals_get(date_from, now)
    result = []
    if deals:
        # Gom nhóm deal theo position_id để lấy chuẩn xác Open Price và Close Price từ sàn
        deals_by_pos = {}
        for d in deals:
            # Loại bỏ các giao dịch nạp/rút tiền (DEAL_TYPE_BALANCE = 2)
            if getattr(d, 'type', 0) == 2:
                continue
            sym_raw = str(getattr(d, 'symbol', '') or '').strip()
            if not sym_raw:
                continue

            pos_id = str(d.position_id or d.ticket)
            if pos_id not in deals_by_pos:
                deals_by_pos[pos_id] = {'in': None, 'out': None, 'all': []}
            deals_by_pos[pos_id]['all'].append(d)
            if d.entry == 0: # Deal vào lệnh (IN)
                deals_by_pos[pos_id]['in'] = d
            elif d.entry == 1 or d.profit != 0: # Deal đóng lệnh (OUT)
                deals_by_pos[pos_id]['out'] = d

        for pos_id, grp in deals_by_pos.items():
            out_deal = grp['out']
            in_deal = grp['in']
            # Chỉ lấy các lệnh ĐÃ ĐÓNG THỰC SỰ (bắt buộc phải có deal thoát lệnh out_deal)
            # Lệnh chưa đóng (chỉ có in_deal) là vị thế đang chạy, không được đưa vào lịch sử
            if not out_deal:
                continue

            target_deal = out_deal or in_deal
            sym = str(getattr(target_deal, 'symbol', '') or '').strip()
            if not sym:
                continue

            # Xác định chiều vị thế ban đầu (BUY / SELL)
            # MT5 Rule:
            # - Khi mở vị thế BUY: in_deal.type = 0 (BUY), out_deal.type = 1 (SELL)
            # - Khi mở vị thế SELL: in_deal.type = 1 (SELL), out_deal.type = 0 (BUY)
            if in_deal:
                deal_type = 'BUY' if in_deal.type == 0 else 'SELL'
                open_price = float(in_deal.price)
                open_time = in_deal.time
            elif out_deal:
                # Nếu chỉ có deal thoát: deal thoát là SELL (1) nghĩa là vị thế gốc là BUY
                deal_type = 'BUY' if out_deal.type == 1 else 'SELL'
                open_price = float(out_deal.price)
                open_time = out_deal.time
            else:
                deal_type = 'BUY' if target_deal.type == 0 else 'SELL'
                open_price = float(target_deal.price)
                open_time = target_deal.time

            close_price = float(out_deal.price) if out_deal else open_price
            close_time = out_deal.time if out_deal else open_time

            all_prices = [float(getattr(d, 'price', 0.0) or 0.0) for d in grp['all'] if float(getattr(d, 'price', 0.0) or 0.0) > 0]
            if open_price == 0.0 and all_prices:
                open_price = all_prices[0]
            if close_price == 0.0 and all_prices:
                close_price = all_prices[-1]

            total_profit = float(sum(getattr(d, 'profit', 0.0) or 0.0 for d in grp['all']))
            total_comm = float(sum(getattr(d, 'commission', 0.0) or 0.0 for d in grp['all']))
            total_swap = float(sum(getattr(d, 'swap', 0.0) or 0.0 for d in grp['all']))
            total_vol = float(target_deal.volume)

            comment = str(target_deal.comment or '').strip()
            magic = int(getattr(in_deal, 'magic', 0) or target_deal.magic or 0)
            close_magic = int(getattr(out_deal, 'magic', 0) or 0) if out_deal else int(target_deal.magic or 0)
            cu = comment.upper()
            if any(k in cu for k in ('WEBMANUAL', 'WEB CLOSE', 'WEB-CLOSE', 'CLOSEALL', 'CLOSE ALL', 'MANUAL')):
                source = 'USER'
            else:
                is_bot = (magic == 8882026) or cu.startswith('BOT') or cu.startswith('AI') or any(
                    k in comment.lower() for k in ['autobot', 'scalp', 'bot_auto', 'ai_scalp']
                )
                source = 'BOT' if is_bot else 'USER'
            deal_reason = int(getattr(out_deal or target_deal, 'reason', -1) or -1)

            result.append({
                'ticket': pos_id,
                'deal_ticket': str(target_deal.ticket),
                'symbol': sym,
                'position_type': deal_type,
                'lot_size': total_vol,
                'open_price': open_price,
                'close_price': close_price,
                'profit': total_profit,
                'commission': total_comm,
                'swap': total_swap,
                'time': close_time,
                'open_time': open_time,
                'magic': close_magic if source == 'USER' else magic,
                'comment': comment,
                'reason': deal_reason,
                'source': source
            })

    return jsonify({'success': True, 'deals': result})

def send_via_native_mql5(action, params_str):
    """Gửi lệnh thực thi trực tiếp qua Native MQL5 Bridge trong MT5."""
    candidates = [
        r"C:\users\ubuntu\AppData\Roaming\MetaQuotes\Terminal\Common\Files",
        "/home/ubuntu/.mt5/drive_c/users/ubuntu/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
    ]
    common_files_dir = None
    for c in candidates:
        if os.path.exists(c):
            common_files_dir = c
            break

    if not common_files_dir:
        return False, "Không tìm thấy thư mục MQL5 Common Files", {}

    cmd_file = os.path.join(common_files_dir, "commands.txt")
    resp_file = os.path.join(common_files_dir, "responses.txt")

    if os.path.exists(resp_file):
        try:
            os.remove(resp_file)
        except Exception:
            pass

    full_cmd = f"{action}|{params_str}"
    try:
        with open(cmd_file, "w", encoding="ascii") as f:
            f.write(full_cmd)
    except Exception as e:
        return False, f"Lỗi ghi lệnh Native MQL5: {e}", {}

    start_t = time.time()
    while time.time() - start_t < 2.0:
        if os.path.exists(resp_file):
            time.sleep(0.05)
            try:
                with open(resp_file, "r", encoding="ascii") as f:
                    content = f.read().strip()
                try:
                    os.remove(resp_file)
                except Exception:
                    pass
                if content:
                    parts = content.split("|")
                    st = parts[0]
                    msg = parts[1] if len(parts) > 1 else ""
                    deal = parts[2] if len(parts) > 2 else ""
                    price = float(parts[3]) if len(parts) > 3 and parts[3] else 0.0
                    order_ticket = parts[4] if len(parts) > 4 else ""
                    if st == "OK":
                        return True, msg, {'deal': deal, 'price': price, 'ticket': order_ticket}
                    else:
                        return False, msg, {}
            except Exception:
                pass
        time.sleep(0.05)

    return False, "Hết thời gian chờ phản hồi từ Native MQL5 Bridge", {}

@app.route('/order_send', methods=['POST'])
def send_order():
    ok, msg = ensure_mt5_init()
    if not ok:
        return jsonify({'success': False, 'error': msg}), 400

    data = request.get_json(force=True)
    symbol = str(data.get('symbol', '')).strip()
    order_type = str(data.get('order_type', 'BUY')).upper()
    volume = float(data.get('volume', 0.01))
    price = float(data.get('price', 0.0))
    sl = float(data.get('sl', 0.0))
    tp = float(data.get('tp', 0.0))
    comment = str(data.get('comment', 'Web Auto Trade'))

    # Check symbol suffix and select into Market Watch
    candidates = [symbol, f"{symbol}m", f"{symbol}_i", f"{symbol}c", symbol.upper()]
    sym_name = symbol
    sym_info = None
    for cand in candidates:
        try:
            mt5.symbol_select(cand, True)
            info = mt5.symbol_info(cand)
            if info:
                sym_name = cand
                sym_info = info
                break
        except Exception:
            pass

    mt5_type = mt5.ORDER_TYPE_BUY if order_type == 'BUY' else mt5.ORDER_TYPE_SELL

    modes_to_try = []
    if sym_info and sym_info.filling_mode:
        if sym_info.filling_mode & 1:
            modes_to_try.append(mt5.ORDER_FILLING_FOK)
        if sym_info.filling_mode & 2:
            modes_to_try.append(mt5.ORDER_FILLING_IOC)
        if sym_info.filling_mode & 4:
            modes_to_try.append(mt5.ORDER_FILLING_RETURN)
    if not modes_to_try:
        modes_to_try = [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]

    last_res = None
    for f_mode in modes_to_try:
        tick = mt5.symbol_info_tick(sym_name)
        current_price = float(tick.ask if order_type == 'BUY' else tick.bid) if tick else price
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym_name,
            "volume": volume,
            "type": mt5_type,
            "price": current_price,
            "sl": sl,
            "tp": tp,
            "deviation": 100,
            "magic": 8882026,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": f_mode,
        }

        result = mt5.order_send(req)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"✅ Đã mở thành công lệnh MT5 #{result.order}: {order_type} {sym_name} {volume} Lot tại {result.price}")
            return jsonify({
                'success': True,
                'ticket': str(result.order),
                'deal': str(result.deal),
                'price': float(result.price),
                'volume': float(result.volume),
                'comment': result.comment or comment
            })
        last_res = result

    # Native MQL5 Bridge Fallback
    mql5_params = f"{sym_name}|{volume}|{sl}|{tp}|8882026|{comment}"
    n_ok, n_msg, n_data = send_via_native_mql5(order_type, mql5_params)
    if n_ok:
        logger.info(f"✅ [Native MQL5] Đã mở thành công lệnh {order_type} {sym_name}: {n_msg}")
        return jsonify({
            'success': True,
            'ticket': str(n_data.get('ticket', 'NATIVE')),
            'deal': str(n_data.get('deal', '')),
            'price': float(n_data.get('price', price)),
            'volume': volume,
            'comment': comment
        })

    err = mt5.last_error()
    ret_msg = last_res.comment if last_res else f"Lỗi MT5: {err}"
    if 'autotrading' in str(ret_msg).lower() or 'disabled' in str(ret_msg).lower():
        ret_msg = f"{ret_msg}. Vui lòng kiểm tra trên phần mềm MT5: bấm nút 'Algo Trading' (màu xanh trên thanh công cụ) và tích chọn 'Allow algorithmic trading' trong Tools -> Options -> Expert Advisors."
    return jsonify({'success': False, 'error': ret_msg}), 400

@app.route('/close_order', methods=['POST'])
def close_order():
    ok, msg = ensure_mt5_init()
    if not ok:
        return jsonify({'success': False, 'error': msg}), 400

    data = request.get_json(force=True)
    ticket = int(str(data.get('ticket', 0)).replace('#', '').strip())
    symbol = str(data.get('symbol', '')).strip()
    order_type = str(data.get('order_type', 'BUY')).upper()
    volume = float(data.get('volume', 0.01))

    # 1. Tìm vị thế chính xác theo Ticket trên MT5
    pos = None
    if ticket > 0:
        all_positions = mt5.positions_get()
        if all_positions:
            for p in all_positions:
                if int(p.ticket) == ticket:
                    pos = p
                    break
        if not pos:
            pos_list = mt5.positions_get(ticket=ticket)
            if pos_list:
                pos = pos_list[0]

    if pos:
        sym_name = pos.symbol
        volume = float(pos.volume)
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    else:
        # Nếu lệnh không còn trong danh sách mở MT5 (đã đóng do SL/TP/sàn)
        if ticket > 0:
            logger.info(f"ℹ️ Vị thế #{ticket} không còn trên MT5 (đã đóng trước đó).")
            return jsonify({
                'success': True,
                'message': f'Vị thế #{ticket} đã đóng trước đó trên sàn MT5',
                'already_closed': True,
                'ticket': str(ticket)
            })

        # Fallback if position closed or ticket not specified
        candidates = [symbol, f"{symbol}m", f"{symbol}_i", f"{symbol}c"]
        sym_name = symbol
        for cand in candidates:
            if mt5.symbol_info(cand):
                sym_name = cand
                break
        close_type = mt5.ORDER_TYPE_SELL if order_type == 'BUY' else mt5.ORDER_TYPE_BUY

    mt5.symbol_select(sym_name, True)
    sym_info = mt5.symbol_info(sym_name)

    modes_to_try = []
    if sym_info and sym_info.filling_mode:
        if sym_info.filling_mode & 1:
            modes_to_try.append(mt5.ORDER_FILLING_FOK)
        if sym_info.filling_mode & 2:
            modes_to_try.append(mt5.ORDER_FILLING_IOC)
        if sym_info.filling_mode & 4:
            modes_to_try.append(mt5.ORDER_FILLING_RETURN)
    if not modes_to_try:
        modes_to_try = [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]

    last_res = None
    for f_mode in modes_to_try:
        tick = mt5.symbol_info_tick(sym_name)
        if not tick:
            continue
        price = float(tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask)
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": sym_name,
            "volume": volume,
            "type": close_type,
            "price": price,
            "deviation": 100,
            "magic": int(data.get('magic') or 0),
            "comment": str(data.get('comment') or 'WebManual')[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": f_mode,
        }

        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"✅ Đã đóng thành công lệnh #{ticket} trên MT5: {sym_name} {volume} Lot tại {res.price}")
            return jsonify({
                'success': True,
                'message': f'Đã đóng thành công lệnh #{ticket} trên Exness MT5',
                'deal': str(res.deal),
                'ticket': str(ticket),
                'price': float(res.price)
            })
        last_res = res

    # Native MQL5 Bridge Fallback
    mql5_close_params = f"{ticket}|{sym_name}|{volume}"
    n_ok, n_msg, n_data = send_via_native_mql5("CLOSE", mql5_close_params)
    if n_ok:
        logger.info(f"✅ [Native MQL5] Đã đóng thành công lệnh #{ticket}: {n_msg}")
        return jsonify({
            'success': True,
            'message': f'Đã đóng thành công lệnh #{ticket} trên Exness MT5',
            'deal': str(n_data.get('deal', '')),
            'ticket': str(ticket),
            'price': float(n_data.get('price', 0.0))
        })

    err_msg = last_res.comment if last_res else str(mt5.last_error())
    retcode = last_res.retcode if last_res else -1
    logger.warning(f"❌ MT5 đóng lệnh #{ticket} thất bại: retcode={retcode}, error={err_msg}")
    if 'autotrading' in str(err_msg).lower() or 'disabled' in str(err_msg).lower():
        err_msg = f"{err_msg}. Vui lòng bật nút 'Algo Trading' (màu xanh trên thanh công cụ MT5) và tích chọn 'Allow algorithmic trading' trong Tools -> Options -> Expert Advisors."
    return jsonify({'success': False, 'error': f'MT5 đóng lệnh thất bại: {err_msg}'}), 400


@app.route('/order_modify', methods=['POST'])
def modify_order_endpoint():
    """Dời SL và TP trên sàn Exness MT5 để bảo vệ và tối đa hóa lợi nhuận."""
    try:
        data = request.get_json(force=True) or {}
        ticket = int(data.get('ticket', 0) or 0)
        sl = float(data.get('sl', 0.0) or 0.0)
        tp = float(data.get('tp', 0.0) or 0.0)
        symbol = str(data.get('symbol', '') or '')

        ok, msg = ensure_mt5_init()
        if not ok:
            return jsonify({'success': False, 'error': msg}), 503

        if ticket <= 0:
            return jsonify({'success': False, 'error': 'Ticket không hợp lệ'}), 400

        pos = None
        try:
            found = mt5.positions_get(ticket=ticket)
            if found:
                pos = found[0]
        except Exception:
            pos = None
        if pos is None:
            all_pos = mt5.positions_get() or []
            for p in all_pos:
                if int(getattr(p, 'ticket', 0) or 0) == ticket:
                    pos = p
                    break
        if pos is None:
            return jsonify({'success': False, 'error': f'Không tìm thấy vị thế #{ticket} trên MT5'}), 404

        broker_symbol = pos.symbol or symbol
        digits = 5
        try:
            info = mt5.symbol_info(broker_symbol)
            if info:
                digits = int(getattr(info, 'digits', 5) or 5)
        except Exception:
            pass
        sl_r = round(sl, digits) if sl > 0 else 0.0
        tp_r = round(tp, digits) if tp > 0 else 0.0

        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": broker_symbol,
            "sl": sl_r,
            "tp": tp_r,
            "magic": int(getattr(pos, 'magic', 0) or 0),
        }

        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"✅ Đã dời SL ({sl_r}) và TP ({tp_r}) thành công cho lệnh #{ticket}")
            return jsonify({
                'success': True,
                'message': f'Đã dời SL ({sl_r}) và TP ({tp_r}) thành công cho lệnh #{ticket}',
                'ticket': str(ticket),
                'sl': sl_r,
                'tp': tp_r
            })

        n_ok, n_msg, n_data = send_via_native_mql5("MODIFY", f"{ticket}|{sl_r}|{tp_r}|{broker_symbol}")
        if n_ok:
            logger.info(f"✅ [Native MQL5] Đã dời SL ({sl_r}) TP ({tp_r}) lệnh #{ticket}")
            return jsonify({
                'success': True,
                'message': n_msg or f'Đã dời SL ({sl_r}) thành công cho lệnh #{ticket}',
                'ticket': str(ticket),
                'sl': sl_r,
                'tp': tp_r,
            })

        err_msg = res.comment if res else str(mt5.last_error())
        return jsonify({'success': False, 'error': f'Lỗi MT5 dời SL/TP: {err_msg}'}), 400
    except Exception as e:
        logger.exception("order_modify crashed")
        return jsonify({'success': False, 'error': f'Lỗi bridge dời SL/TP: {e}'}), 500


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
    logger.info(f"🚀 Khởi động MT5 Wine Bridge Server tại http://0.0.0.0:{port}...")
    app.run(host='0.0.0.0', port=port, debug=False)

