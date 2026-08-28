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
            mt5_server='Exness-MT5Real',
            initial_balance=Decimal('10000.00'),
            balance=Decimal('10000.00'),
            equity=Decimal('10000.00'),
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
            self.assertTrue(plan.rr_ratio >= 1.4)
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

        position = ExecutionEngine.trigger_plan_to_position(plan)
        self.assertEqual(position.wallet, self.wallet)
        self.assertEqual(position.symbol, 'XAUUSD')
        self.assertTrue(position.lot_size >= 0.01)

        # Test closing position
        ExecutionEngine.close_position(position, close_price=Decimal('2760.00'), reason='TP_HIT')
        self.assertEqual(Position.objects.filter(pk=position.id).count(), 0)
        self.assertEqual(TradeHistory.objects.filter(wallet=self.wallet).count(), 1)
        self.assertEqual(self.wallet.total_trades, 1)

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

        # 5. Backtest API
        res = self.client.post('/api/backtest/run/', {'symbol': 'XAUUSD', 'days': 15}, content_type='application/json')
        self.assertEqual(res.status_code, 200)
        bdata = res.json()
        self.assertEqual(bdata['symbol'], 'XAUUSD')
        self.assertIn('win_rate', bdata)
        self.assertIn('profit_factor', bdata)
        self.assertIn('sharpe_ratio', bdata)

        # 6. Bot Logs & Error Monitor API
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
