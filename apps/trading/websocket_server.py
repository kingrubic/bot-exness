import asyncio
import json
import logging
import threading
from django.utils import timezone
import websockets

logger = logging.getLogger(__name__)

CONNECTED_CLIENTS = set()

def _fetch_live_ticks_data_safe():
    try:
        from apps.api.views import build_live_ticks_data
        return build_live_ticks_data()
    finally:
        from django.db import connection
        connection.close()

async def tick_broadcaster():
    """
    Vòng lặp bất đồng bộ phát sóng giá Live & Vị thế tới toàn bộ WebSocket Clients kết nối.
    - Hoàn toàn 0 request lặp lại (1 kết nối Socket duy nhất).
    - Tốc độ phát sóng thời gian thực cực nhanh (500ms/tick).
    """
    while True:
        try:
            if CONNECTED_CLIENTS:
                loop = asyncio.get_event_loop()
                data = await loop.run_in_executor(None, _fetch_live_ticks_data_safe)
                message = json.dumps(data)
                
                # Broadcast đồng thời tới tất cả clients
                websockets_to_remove = set()
                for ws in list(CONNECTED_CLIENTS):
                    try:
                        await ws.send(message)
                    except Exception:
                        websockets_to_remove.add(ws)
                
                for ws in websockets_to_remove:
                    CONNECTED_CLIENTS.discard(ws)
        except Exception as e:
            logger.debug(f"WebSocket broadcast error: {e}")
            
        await asyncio.sleep(0.5)

async def handle_client(websocket):
    """Xử lý kết nối client mới vào WebSocket."""
    CONNECTED_CLIENTS.add(websocket)
    try:
        loop = asyncio.get_event_loop()
        init_data = await loop.run_in_executor(None, _fetch_live_ticks_data_safe)
        await websocket.send(json.dumps(init_data))
        
        async for message in websocket:
            pass
    except Exception:
        pass
    finally:
        CONNECTED_CLIENTS.discard(websocket)

async def main_websocket_server(host='0.0.0.0', port=8889):
    """Khởi chạy WebSocket Server."""
    print(f"🔌 [WEBSOCKET] Khởi động Realtime WebSocket Server tại ws://{host}:{port}...")
    async with websockets.serve(handle_client, host, port):
        await tick_broadcaster()

def start_websocket_server_thread(host='0.0.0.0', port=8889):
    """Hàm chạy WebSocket Server trong một luồng nền riêng biệt."""
    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(main_websocket_server(host, port))
        except Exception as e:
            print(f"⚠️ [WEBSOCKET ERROR] Lỗi WebSocket Server: {e}")

    ws_thread = threading.Thread(target=run, daemon=True)
    ws_thread.start()
    return ws_thread
