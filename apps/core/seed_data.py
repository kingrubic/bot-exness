import os
import sys
from pathlib import Path
from decimal import Decimal

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR / 'packages'))
sys.path.insert(0, str(BASE_DIR))

import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'exness_project.settings')
django.setup()

from apps.symbols.models import SymbolConfig
from apps.analysis.analyzer import TechnicalAnalyzer
from apps.accounts.models import ExnessServerMaster
from apps.core.trading_defaults import apply_default_active_symbols

def run_seed():
    """
    Khởi tạo toàn bộ Master Data chuẩn sàn Exness:
    - 50+ Servers (MT5 Real, MT5 Trial, MT4 Real, MT4 Trial)
    - Toàn bộ các loại tài khoản Exness (Standard, Raw Spread, Zero, Pro, Cent, Demo...)
    - Toàn bộ danh mục cặp giao dịch Exness (Vàng, Bạc, Forex Majors, Minors, Crypto, Chỉ Số, Năng Lượng)
    """
    print("📈 Đang khởi tạo danh mục các cặp giao dịch chuẩn Exness...")

    symbols_data = [
        # ===== KIM LOẠI QUÝ (METALS) =====
        {
            'symbol': 'XAUUSD',
            'display_name': 'Gold vs US Dollar (Vàng)',
            'category': 'METALS',
            'digits': 2,
            'point_size': 0.01,
            'contract_size': 100.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('2750.50'),
            'current_bid': Decimal('2750.35'),
            'current_ask': Decimal('2750.65'),
            'current_spread_pips': 1.2,
            'max_allowed_spread': 3.5,
            'atr_value': 14.00,
            'is_active': True,
        },
        {
            'symbol': 'XAGUSD',
            'display_name': 'Silver vs US Dollar (Bạc)',
            'category': 'METALS',
            'digits': 3,
            'point_size': 0.001,
            'contract_size': 5000.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('32.450'),
            'current_bid': Decimal('32.435'),
            'current_ask': Decimal('32.465'),
            'current_spread_pips': 1.5,
            'max_allowed_spread': 4.0,
            'atr_value': 0.35,
            'is_active': True,
        },
        {
            'symbol': 'XPTUSD',
            'display_name': 'Platinum vs US Dollar',
            'category': 'METALS',
            'digits': 2,
            'point_size': 0.01,
            'contract_size': 100.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('1020.00'),
            'current_bid': Decimal('1019.50'),
            'current_ask': Decimal('1020.50'),
            'current_spread_pips': 2.0,
            'max_allowed_spread': 5.0,
            'atr_value': 8.5,
            'is_active': True,
        },
        {
            'symbol': 'XAUEUR',
            'display_name': 'Gold vs Euro',
            'category': 'METALS',
            'digits': 2,
            'point_size': 0.01,
            'contract_size': 100.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('2535.00'),
            'current_bid': Decimal('2534.50'),
            'current_ask': Decimal('2535.50'),
            'current_spread_pips': 1.8,
            'max_allowed_spread': 4.5,
            'atr_value': 12.0,
            'is_active': True,
        },

        # ===== NGOẠI HỐI CHÍNH (FOREX MAJORS) =====
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
            'symbol': 'USDJPY',
            'display_name': 'US Dollar vs Japanese Yen',
            'category': 'FOREX',
            'digits': 3,
            'point_size': 0.001,
            'contract_size': 100000.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('153.200'),
            'current_bid': Decimal('153.193'),
            'current_ask': Decimal('153.207'),
            'current_spread_pips': 0.7,
            'max_allowed_spread': 2.0,
            'atr_value': 0.65,
            'is_active': True,
        },
        {
            'symbol': 'USDCHF',
            'display_name': 'US Dollar vs Swiss Franc',
            'category': 'FOREX',
            'digits': 5,
            'point_size': 0.00001,
            'contract_size': 100000.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('0.86700'),
            'current_bid': Decimal('0.86692'),
            'current_ask': Decimal('0.86708'),
            'current_spread_pips': 0.8,
            'max_allowed_spread': 2.0,
            'atr_value': 0.0038,
            'is_active': True,
        },
        {
            'symbol': 'AUDUSD',
            'display_name': 'Australian Dollar vs US Dollar',
            'category': 'FOREX',
            'digits': 5,
            'point_size': 0.00001,
            'contract_size': 100000.0,
            'timeframe': 'M15',
            'strategy': 'SCALPING_BB',
            'current_price': Decimal('0.65800'),
            'current_bid': Decimal('0.65793'),
            'current_ask': Decimal('0.65807'),
            'current_spread_pips': 0.7,
            'max_allowed_spread': 2.0,
            'atr_value': 0.0040,
            'is_active': True,
        },
        {
            'symbol': 'USDCAD',
            'display_name': 'US Dollar vs Canadian Dollar',
            'category': 'FOREX',
            'digits': 5,
            'point_size': 0.00001,
            'contract_size': 100000.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('1.38900'),
            'current_bid': Decimal('1.38891'),
            'current_ask': Decimal('1.38909'),
            'current_spread_pips': 0.9,
            'max_allowed_spread': 2.2,
            'atr_value': 0.0042,
            'is_active': True,
        },
        {
            'symbol': 'NZDUSD',
            'display_name': 'New Zealand Dollar vs US Dollar',
            'category': 'FOREX',
            'digits': 5,
            'point_size': 0.00001,
            'contract_size': 100000.0,
            'timeframe': 'M15',
            'strategy': 'SCALPING_BB',
            'current_price': Decimal('0.59700'),
            'current_bid': Decimal('0.59691'),
            'current_ask': Decimal('0.59709'),
            'current_spread_pips': 0.9,
            'max_allowed_spread': 2.2,
            'atr_value': 0.0035,
            'is_active': True,
        },

        # ===== NGOẠI HỐI PHỤ & CHÉO (FOREX MINORS & CROSSES) =====
        {
            'symbol': 'EURGBP',
            'display_name': 'Euro vs British Pound',
            'category': 'FOREX',
            'digits': 5,
            'point_size': 0.00001,
            'contract_size': 100000.0,
            'timeframe': 'M15',
            'strategy': 'SCALPING_BB',
            'current_price': Decimal('0.83550'),
            'current_bid': Decimal('0.83542'),
            'current_ask': Decimal('0.83558'),
            'current_spread_pips': 0.8,
            'max_allowed_spread': 2.0,
            'atr_value': 0.0028,
            'is_active': True,
        },
        {
            'symbol': 'EURJPY',
            'display_name': 'Euro vs Japanese Yen',
            'category': 'FOREX',
            'digits': 3,
            'point_size': 0.001,
            'contract_size': 100000.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('166.250'),
            'current_bid': Decimal('166.241'),
            'current_ask': Decimal('166.259'),
            'current_spread_pips': 0.9,
            'max_allowed_spread': 2.5,
            'atr_value': 0.75,
            'is_active': True,
        },
        {
            'symbol': 'GBPJPY',
            'display_name': 'British Pound vs Japanese Yen (Guppy)',
            'category': 'FOREX',
            'digits': 3,
            'point_size': 0.001,
            'contract_size': 100000.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('198.800'),
            'current_bid': Decimal('198.788'),
            'current_ask': Decimal('198.812'),
            'current_spread_pips': 1.2,
            'max_allowed_spread': 3.0,
            'atr_value': 1.10,
            'is_active': True,
        },
        {
            'symbol': 'AUDJPY',
            'display_name': 'Australian Dollar vs Japanese Yen',
            'category': 'FOREX',
            'digits': 3,
            'point_size': 0.001,
            'contract_size': 100000.0,
            'timeframe': 'M15',
            'strategy': 'SCALPING_BB',
            'current_price': Decimal('100.850'),
            'current_bid': Decimal('100.840'),
            'current_ask': Decimal('100.860'),
            'current_spread_pips': 1.0,
            'max_allowed_spread': 2.5,
            'atr_value': 0.55,
            'is_active': True,
        },

        # ===== TIỀN MÃ HÓA (CRYPTO) =====
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
            'symbol': 'ETHUSD',
            'display_name': 'Ethereum vs US Dollar',
            'category': 'CRYPTO',
            'digits': 2,
            'point_size': 0.01,
            'contract_size': 1.0,
            'timeframe': 'H1',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('3250.00'),
            'current_bid': Decimal('3249.50'),
            'current_ask': Decimal('3250.50'),
            'current_spread_pips': 2.0,
            'max_allowed_spread': 6.0,
            'atr_value': 65.0,
            'is_active': True,
        },
        {
            'symbol': 'SOLUSD',
            'display_name': 'Solana vs US Dollar',
            'category': 'CRYPTO',
            'digits': 2,
            'point_size': 0.01,
            'contract_size': 1.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('185.50'),
            'current_bid': Decimal('185.35'),
            'current_ask': Decimal('185.65'),
            'current_spread_pips': 2.5,
            'max_allowed_spread': 6.5,
            'atr_value': 4.5,
            'is_active': True,
        },
        {
            'symbol': 'BNBUSD',
            'display_name': 'Binance Coin vs US Dollar',
            'category': 'CRYPTO',
            'digits': 2,
            'point_size': 0.01,
            'contract_size': 1.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('620.00'),
            'current_bid': Decimal('619.60'),
            'current_ask': Decimal('620.40'),
            'current_spread_pips': 2.2,
            'max_allowed_spread': 6.0,
            'atr_value': 10.0,
            'is_active': True,
        },

        # ===== CHỈ SỐ CHỨNG KHOÁN (INDICES) =====
        {
            'symbol': 'US30',
            'display_name': 'Dow Jones Industrial Average 30',
            'category': 'INDICES',
            'digits': 2,
            'point_size': 0.01,
            'contract_size': 1.0,
            'timeframe': 'M15',
            'strategy': 'BREAKOUT_SESSION',
            'current_price': Decimal('43850.00'),
            'current_bid': Decimal('43848.00'),
            'current_ask': Decimal('43852.00'),
            'current_spread_pips': 2.0,
            'max_allowed_spread': 6.0,
            'atr_value': 220.0,
            'is_active': True,
        },
        {
            'symbol': 'USTEC',
            'display_name': 'Nasdaq 100 Index (NAS100)',
            'category': 'INDICES',
            'digits': 2,
            'point_size': 0.01,
            'contract_size': 1.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('20950.00'),
            'current_bid': Decimal('20948.50'),
            'current_ask': Decimal('20951.50'),
            'current_spread_pips': 1.5,
            'max_allowed_spread': 5.0,
            'atr_value': 140.0,
            'is_active': True,
        },
        {
            'symbol': 'US500',
            'display_name': 'S&P 500 Index',
            'category': 'INDICES',
            'digits': 2,
            'point_size': 0.01,
            'contract_size': 1.0,
            'timeframe': 'M15',
            'strategy': 'SMC_TREND',
            'current_price': Decimal('5920.00'),
            'current_bid': Decimal('5919.50'),
            'current_ask': Decimal('5920.50'),
            'current_spread_pips': 1.0,
            'max_allowed_spread': 3.5,
            'atr_value': 35.0,
            'is_active': True,
        },
        {
            'symbol': 'DE40',
            'display_name': 'Germany DAX 40',
            'category': 'INDICES',
            'digits': 2,
            'point_size': 0.01,
            'contract_size': 1.0,
            'timeframe': 'M15',
            'strategy': 'BREAKOUT_SESSION',
            'current_price': Decimal('19450.00'),
            'current_bid': Decimal('19448.00'),
            'current_ask': Decimal('19452.00'),
            'current_spread_pips': 1.8,
            'max_allowed_spread': 5.0,
            'atr_value': 90.0,
            'is_active': True,
        },

        # ===== NĂNG LƯỢNG & HÀNG HÓA (COMMODITIES & ENERGIES) =====
        {
            'symbol': 'USOIL',
            'display_name': 'WTI Crude Oil (Dầu thô Mỹ)',
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
        },
        {
            'symbol': 'UKOIL',
            'display_name': 'Brent Crude Oil (Dầu Brent)',
            'category': 'COMMODITIES',
            'digits': 2,
            'point_size': 0.01,
            'contract_size': 1000.0,
            'timeframe': 'M15',
            'strategy': 'SCALPING_BB',
            'current_price': Decimal('78.80'),
            'current_bid': Decimal('78.77'),
            'current_ask': Decimal('78.83'),
            'current_spread_pips': 1.8,
            'max_allowed_spread': 4.0,
            'atr_value': 1.25,
            'is_active': True,
        },
        {
            'symbol': 'XNGUSD',
            'display_name': 'Natural Gas (Khí tự nhiên)',
            'category': 'COMMODITIES',
            'digits': 3,
            'point_size': 0.001,
            'contract_size': 10000.0,
            'timeframe': 'M15',
            'strategy': 'SCALPING_BB',
            'current_price': Decimal('2.750'),
            'current_bid': Decimal('2.742'),
            'current_ask': Decimal('2.758'),
            'current_spread_pips': 2.5,
            'max_allowed_spread': 6.0,
            'atr_value': 0.08,
            'is_active': True,
        }
    ]

    for s_info in symbols_data:
        sym, _ = SymbolConfig.objects.update_or_create(
            symbol=s_info['symbol'],
            defaults=s_info
        )
        TechnicalAnalyzer.generate_market_analysis(sym)

    # Khởi tạo Master Data Server Exness (50 MT5 Real & 50 MT5 Demo/Trial)
    print("🌐 Đang khởi tạo danh mục Master Data Server Exness (50 MT5 Real & 50 MT5 Demo)...")
    
    # Xóa các server MT4 cũ không thuộc MT5
    ExnessServerMaster.objects.filter(server_name__startswith='Exness-Real').delete()
    ExnessServerMaster.objects.filter(server_name__startswith='Exness-Trial').delete()

    # Exness MT5 Real Servers (1 to 50)
    for i in range(1, 51):
        s_name = "Exness-MT5Real" if i == 1 else f"Exness-MT5Real{i}"
        ExnessServerMaster.objects.update_or_create(
            server_name=s_name,
            defaults={
                'server_type': 'REAL',
                'description': f"Máy chủ MT5 Real #{i}",
                'is_active': True,
                'order': i
            }
        )

    # Exness MT5 Trial / Demo Servers (1 to 50)
    for i in range(1, 51):
        s_name = "Exness-MT5Trial" if i == 1 else f"Exness-MT5Trial{i}"
        ExnessServerMaster.objects.update_or_create(
            server_name=s_name,
            defaults={
                'server_type': 'DEMO',
                'description': f"Máy chủ MT5 Demo #{i}",
                'is_active': True,
                'order': 50 + i
            }
        )

    print("✅ Đã hoàn tất khởi tạo 100 Server MT5 Exness (50 Real + 50 Demo)!")
    apply_default_active_symbols()


if __name__ == '__main__':
    run_seed()
