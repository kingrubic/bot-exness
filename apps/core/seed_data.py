import os
import sys
from pathlib import Path
from decimal import Decimal

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR / 'packages'))
sys.path.insert(0, str(BASE_DIR))

import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'exness_project.settings')
django.setup()

from django.utils import timezone
from apps.symbols.models import SymbolConfig
from apps.analysis.analyzer import TechnicalAnalyzer

def run_seed():
    """
    Khởi tạo danh mục các cặp giao dịch tiêu chuẩn sàn Exness.
    Không tạo sẵn ví hay lệnh ảo - Admin tự tạo và kết nối ví theo ý muốn.
    """
    print("📈 Đang khởi tạo danh mục các cặp giao dịch Exness...")

    symbols_data = [
        {
            'symbol': 'XAUUSD',
            'display_name': 'Gold vs US Dollar (Vàng)',
            'category': 'METALS',
            'digits': 2,
            'point_size': 0.01,
            'contract_size': 100.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('2750.00'),
            'current_bid': Decimal('2749.85'),
            'current_ask': Decimal('2750.15'),
            'current_spread_pips': 1.2,
            'max_allowed_spread': 3.5,
            'atr_value': 14.00,
            'is_active': True,
        },
        {
            'symbol': 'EURUSD',
            'display_name': 'Euro vs US Dollar',
            'category': 'FOREX',
            'digits': 5,
            'point_size': 0.00001,
            'contract_size': 100000.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('1.08500'),
            'current_bid': Decimal('1.08494'),
            'current_ask': Decimal('1.08506'),
            'current_spread_pips': 0.6,
            'max_allowed_spread': 1.5,
            'atr_value': 0.0045,
            'is_active': True,
        },
        {
            'symbol': 'GBPUSD',
            'display_name': 'British Pound vs US Dollar',
            'category': 'FOREX',
            'digits': 5,
            'point_size': 0.00001,
            'contract_size': 100000.0,
            'timeframe': 'M15',
            'strategy': 'BREAKOUT_SESSION',
            'current_price': Decimal('1.29800'),
            'current_bid': Decimal('1.29792'),
            'current_ask': Decimal('1.29808'),
            'current_spread_pips': 0.8,
            'max_allowed_spread': 2.0,
            'atr_value': 0.0055,
            'is_active': True,
        },
        {
            'symbol': 'BTCUSD',
            'display_name': 'Bitcoin vs US Dollar',
            'category': 'CRYPTO',
            'digits': 2,
            'point_size': 0.01,
            'contract_size': 1.0,
            'timeframe': 'H1',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('89000.00'),
            'current_bid': Decimal('88995.00'),
            'current_ask': Decimal('89005.00'),
            'current_spread_pips': 3.5,
            'max_allowed_spread': 8.0,
            'atr_value': 1200.0,
            'is_active': True,
        },
        {
            'symbol': 'USOIL',
            'display_name': 'Crude Oil (Dầu thô)',
            'category': 'COMMODITIES',
            'digits': 2,
            'point_size': 0.01,
            'contract_size': 1000.0,
            'timeframe': 'M15',
            'strategy': 'SCALPING_BB',
            'current_price': Decimal('74.50'),
            'current_bid': Decimal('74.47'),
            'current_ask': Decimal('74.53'),
            'current_spread_pips': 1.8,
            'max_allowed_spread': 4.0,
            'atr_value': 1.20,
            'is_active': True,
        }
    ]

    for s_info in symbols_data:
        sym, _ = SymbolConfig.objects.update_or_create(
            symbol=s_info['symbol'],
            defaults=s_info
        )
        TechnicalAnalyzer.generate_market_analysis(sym)

    print("✅ Đã hoàn tất khởi tạo danh mục cặp giao dịch Exness!")

if __name__ == '__main__':
    run_seed()
