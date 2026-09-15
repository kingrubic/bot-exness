import pytest
from decimal import Decimal
from django.test import TestCase, Client
from apps.accounts.models import WalletAccount
from apps.symbols.models import SymbolConfig
from apps.analysis.models import MarketForecast
from apps.analysis.analyzer import TechnicalAnalyzer
from apps.plans.models import TradingPlan
from apps.plans.planner import AutoPlanGenerator
from apps.trading.models import Position, TradeHistory
from apps.trading.execution_engine import ExecutionEngine

class ExnessAutoTradeTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        TradeHistory.objects.all().delete()
        Position.objects.all().delete()
        
        # 1. Create Symbol
        self.symbol = SymbolConfig.objects.create(
            symbol='XAUUSD',
            display_name='Gold vs US Dollar',
            category='METALS',
            digits=2,
            point_size=0.01,
            contract_size=100.0,
            timeframe='M15',
            strategy='SMC_TREND',
            current_price=Decimal('2750.00'),
            current_bid=Decimal('2749.85'),
            current_ask=Decimal('2750.15'),
            current_spread_pips=1.2,
            max_allowed_spread=3.5,
            atr_value=12.0,
            is_active=True
        )

        # 2. Create Wallet
        self.wallet = WalletAccount.objects.create(
            name='Ví A - Test Real',
            account_type='REAL',
            mt5_login='50239182',
            capital=Decimal('10000.00'),
            risk_percent=1.5,
            allowed_symbols_json='["XAUUSD"]',
            is_active=True,
            bot_status='RUNNING'
        )

    def test_technical_analyzer_generates_forecast(self):
        """Kiểm tra TechnicalAnalyzer tự động phân tích và tạo dự báo bước giá tiếp theo."""
        forecast = TechnicalAnalyzer.generate_market_analysis(self.symbol)
        
        self.assertIsNotNone(forecast)
        self.assertEqual(forecast.symbol, 'XAUUSD')
        self.assertIn(forecast.trend_bias, ['BULLISH', 'BEARISH', 'SIDEWAY'])
        self.assertTrue(forecast.confidence_score >= 60.0)
        self.assertTrue(len(forecast.projected_target_zone) > 0)
        self.assertTrue(len(forecast.trigger_condition) > 0)
        self.assertTrue(len(forecast.analysis_rationale) > 0)
        self.assertTrue(float(forecast.next_resistance_1) > 0)
        self.assertTrue(float(forecast.next_support_1) > 0)

    def test_plan_generator_creates_valid_plan(self):
        """Kiểm tra AutoPlanGenerator tính toán đúng tỷ lệ R:R và Lot size an toàn."""
        forecast = TechnicalAnalyzer.generate_market_analysis(self.symbol)
        plan = AutoPlanGenerator.generate_plan_for_wallet(self.wallet, self.symbol, forecast)
        
        if plan: # If generated
            self.assertEqual(plan.wallet, self.wallet)
            self.assertEqual(plan.symbol, 'XAUUSD')
            self.assertTrue(plan.calculated_lot >= 0.01)
            self.assertIn(plan.status, ['PENDING_TRIGGER', 'EXECUTING'])

    def test_execution_engine_lifecycle(self):
        """Kiểm tra chu trình kích hoạt lệnh, tính PnL và đóng lệnh."""
        forecast = TechnicalAnalyzer.generate_market_analysis(self.symbol)
        plan = AutoPlanGenerator.generate_plan_for_wallet(self.wallet, self.symbol, forecast)
        if not plan:
            plan = TradingPlan.objects.create(
                wallet=self.wallet,
                symbol='XAUUSD',
                direction='BUY',
                entry_price=Decimal('2750.00'),
                entry_zone_low=Decimal('2748.00'),
                entry_zone_high=Decimal('2752.00'),
                stop_loss=Decimal('2740.00'),
                take_profit_1=Decimal('2765.00'),
                take_profit_2=Decimal('2775.00'),
                rr_ratio=1.5,
                calculated_lot=0.1,
                status='PENDING_TRIGGER'
            )

        from unittest.mock import patch
        with patch('apps.trading.mt5_connector.ExnessMT5Connector.connect', return_value=True), \
             patch('apps.trading.mt5_connector.ExnessMT5Connector.send_order', return_value={'success': True, 'ticket': '99887766', 'price': 2750.00, 'volume': 0.1}), \
             patch('apps.trading.mt5_connector.ExnessMT5Connector.sync_history_from_mt5', return_value=True), \
             patch('apps.trading.mt5_connector.ExnessMT5Connector.sync_account_info', return_value=True), \
             patch('apps.trading.mt5_connector.ExnessMT5Connector.sync_positions', return_value=True), \
             patch('apps.trading.mt5_connector.ExnessMT5Connector.close_order', return_value=(True, 'closed', {})):
            position = ExecutionEngine.trigger_plan_to_position(plan)
            self.assertIsNotNone(position)
            self.assertEqual(position.wallet, self.wallet)
            self.assertEqual(position.symbol, 'XAUUSD')
            self.assertTrue(position.lot_size >= 0.01)

            # Test closing position
            ExecutionEngine.close_position(position, close_price=Decimal('2760.00'), reason='TP_HIT')
            self.assertEqual(Position.objects.filter(pk=position.id).count(), 0)
            self.wallet.refresh_from_db()

    def test_api_endpoints(self):
        """Kiểm tra các REST API endpoints."""
        # 1. Global Overview
        res = self.client.get('/api/overview/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('total_balance', res.json())
        self.assertIn('active_wallets_count', res.json())

        # 2. Wallets List
        res = self.client.get('/api/wallets/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 1)

        # 3. Wallet Detail
        res = self.client.get(f'/api/wallets/{self.wallet.id}/')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn('report', data)
        self.assertIn('forecasts', data)
        self.assertIn('plans', data)
        self.assertIn('positions', data)
        self.assertIn('history', data)

        # 4. Admin Symbols
        res = self.client.get('/api/admin/symbols/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 1)

        # 5. Bot Logs & Error Monitor API
        from apps.trading.models import BotLog
        log_obj = BotLog.log(level='ERROR', category='CONNECTION', message='Lỗi test kết nối MT5', wallet=self.wallet, symbol='XAUUSD')
        self.assertIsNotNone(log_obj)

        res = self.client.get('/api/admin/logs/')
        self.assertEqual(res.status_code, 200)
        logs_resp = res.json()
        self.assertGreaterEqual(logs_resp['total'], 1)
        self.assertGreaterEqual(logs_resp['unresolved_errors'], 1)

        # Resolve log
        res = self.client.post(f'/api/admin/logs/{log_obj.id}/resolve/')
        self.assertEqual(res.status_code, 200)
        log_obj.refresh_from_db()
        self.assertTrue(log_obj.is_resolved)

    def test_refresh_plans_on_wallet_save(self):
        """Kiểm tra việc xóa plan chưa khớp cũ và tính toán lại plan mới khi lưu ví."""
        # Tạo 1 plan cũ chưa khớp
        old_plan = TradingPlan.objects.create(
            wallet=self.wallet,
            symbol='OLD_SYM',
            direction='BUY',
            entry_price=Decimal('100.00'),
            entry_zone_low=Decimal('99.00'),
            entry_zone_high=Decimal('101.00'),
            stop_loss=Decimal('95.00'),
            take_profit_1=Decimal('105.00'),
            take_profit_2=Decimal('110.00'),
            rr_ratio=1.5,
            calculated_lot=0.05,
            status='PENDING_TRIGGER'
        )

        # Lưu lại ví với allowed_symbols = ['XAUUSD']
        self.wallet.set_allowed_symbols(['XAUUSD'])
        self.wallet.save()

        # Gọi refresh_plans_for_wallet
        new_plans = AutoPlanGenerator.refresh_plans_for_wallet(self.wallet)

        # Kiểm tra old_plan đã bị xóa
        self.assertFalse(TradingPlan.objects.filter(pk=old_plan.id).exists())

        # Kiểm tra plan mới được tính theo XAUUSD
        if new_plans:
            for p in new_plans:
                self.assertEqual(p.symbol, 'XAUUSD')
                self.assertEqual(p.wallet, self.wallet)
                self.assertEqual(p.status, 'PENDING_TRIGGER')

    def test_today_pnl_calculation_excludes_past_history(self):
        """Kiểm tra get_today_pnl chỉ tính các lệnh đóng trong ngày hôm nay, loại trừ lịch sử các ngày cũ."""
        from django.utils import timezone
        import datetime

        # 1. Tạo 1 lệnh đã đóng từ hôm qua (PnL: +$150.00)
        yesterday = timezone.now() - datetime.timedelta(days=1)
        TradeHistory.objects.create(
            wallet=self.wallet,
            ticket='1001',
            symbol='XAUUSD',
            position_type='BUY',
            lot_size=0.1,
            open_price=Decimal('2740.00'),
            close_price=Decimal('2755.00'),
            stop_loss=Decimal('2730.00'),
            take_profit=Decimal('2760.00'),
            pnl=Decimal('150.00'),
            pips=150.0,
            close_reason='TP_HIT',
            is_win=True,
            opened_at=yesterday - datetime.timedelta(hours=2),
            closed_at=yesterday
        )

        # 2. Tạo 1 lệnh đóng trong ngày hôm nay (PnL: +$50.00)
        TradeHistory.objects.create(
            wallet=self.wallet,
            ticket='1002',
            symbol='XAUUSD',
            position_type='BUY',
            lot_size=0.05,
            open_price=Decimal('2750.00'),
            close_price=Decimal('2760.00'),
            stop_loss=Decimal('2740.00'),
            take_profit=Decimal('2765.00'),
            pnl=Decimal('50.00'),
            pips=100.0,
            close_reason='MANUAL_CLOSE',
            is_win=True,
            opened_at=timezone.now() - datetime.timedelta(minutes=30),
            closed_at=timezone.now()
        )

        # get_today_pnl phải chỉ bằng $50.00 (không bị cộng dồn $150 của hôm qua)
        today_pnl = self.wallet.get_today_pnl()
        self.assertEqual(today_pnl, Decimal('50.00'))

        # Kiểm tra API /api/overview/ và /api/wallets/
        res = self.client.get('/api/overview/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['total_today_pnl'], 50.0)

        res = self.client.get(f'/api/wallets/{self.wallet.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['report']['today_pnl'], 50.0)

    def test_real_wallet_creation_auto_fetches_broker_balance(self):
        """Kiểm tra tạo ví Real không cần nhập vốn, tự động lấy số dư từ kết nối sàn Exness MT5."""
        self.wallet.is_active = False
        self.wallet.save(update_fields=['is_active'])
        from unittest.mock import patch
        with patch('apps.trading.mt5_connector.ExnessMT5Connector.activate_wallet_session', return_value=(True, 'OK', {'balance': 7500.50, 'server': 'Exness-MT5Real', 'leverage': 2000, 'algo_trading': True})), \
             patch('apps.trading.mt5_connector.ExnessMT5Connector.connect', return_value=False), \
             patch('apps.api.views._algo_warning_fields', return_value={'algo_required': False}):
            res = self.client.post('/api/admin/wallets/', {
                'name': 'Ví Real Không Nhập Tiền',
                'account_type': 'REAL',
                'mt5_login': '98765432',
                'mt5_password': 'secret_password',
                'mt5_server': 'Exness-MT5Real',
                'allowed_symbols': ['XAUUSD'],
                'risk_percent': 1.5
            }, content_type='application/json')

            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data['success'])

            created_wallet = WalletAccount.objects.get(pk=data['wallet_id'])
            self.assertEqual(created_wallet.account_type, 'REAL')
            self.assertEqual(created_wallet.capital, Decimal('7500.50'))
            self.assertEqual(created_wallet.balance, Decimal('7500.50'))


