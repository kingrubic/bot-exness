from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from apps.accounts.models import WalletAccount
from apps.symbols.models import SymbolConfig
from apps.analysis.models import MarketForecast
from apps.plans.models import TradingPlan

# Plan FAILED/CANCELLED xóa ngay. Plan PENDING lệch hướng forecast xóa ngay.
# Quá STALE_PLAN_SECONDS không được làm mới thì cũng xóa (lướt sóng ngắn hạn).
STALE_PLAN_SECONDS = 30

class AutoPlanGenerator:
    """
    Engine tự động sinh Kế Hoạch Giao Dịch (Trading Plan) cho từng Ví Exness
    dựa trên kết quả phân tích kỹ thuật và tham số quản trị rủi ro.
    """

    EMPTY_WALLET_USD = 1.0

    @staticmethod
    def wallet_has_capital(wallet: WalletAccount, live_equity: float | None = None, live_balance: float | None = None) -> bool:
        """False khi ví ~$0 (thanh lý / hết tiền). Đọc balance_db/equity_db, không fallback sang capital."""
        try:
            if live_equity is not None or live_balance is not None:
                eq = float(live_equity or 0)
                bal = float(live_balance or 0)
            else:
                bal = float(getattr(wallet, 'balance_db', 0) or 0)
                eq = float(getattr(wallet, 'equity_db', 0) or 0)
        except (TypeError, ValueError):
            eq, bal = 0.0, 0.0
        return max(eq, bal) >= AutoPlanGenerator.EMPTY_WALLET_USD

    @classmethod
    def wallet_max_open_trades(cls, wallet: WalletAccount) -> int:
        try:
            n = int(wallet.max_open_trades or 1)
        except (TypeError, ValueError):
            n = 1
        return max(1, min(n, 500))

    @classmethod
    def count_open_positions(cls, wallet: WalletAccount) -> int:
        """Đếm lệnh đang mở: max(DB, MT5) để không lập plan khi đã đủ lệnh."""
        db_n = 0
        try:
            db_n = int(wallet.positions.count())
        except Exception:
            db_n = 0
        live_n = None
        try:
            from apps.trading.execution_engine import ExecutionEngine
            live_n = int(ExecutionEngine.count_open_positions(wallet))
        except Exception:
            live_n = None
        if live_n is None:
            return db_n
        return max(db_n, live_n)

    @classmethod
    def wallet_at_position_cap(cls, wallet: WalletAccount) -> bool:
        """True khi số lệnh mở đã chạm max_open_trades — không lập plan mới."""
        return cls.count_open_positions(wallet) >= cls.wallet_max_open_trades(wallet)

    @classmethod
    def purge_pending_if_at_cap(cls, wallet: WalletAccount | None = None) -> int:
        """Đủ lệnh thì xóa hết plan đang treo (PENDING), không giữ hàng đợi."""
        if wallet is None:
            n = 0
            pending_wids = set(
                TradingPlan.objects.filter(
                    status__in=['PENDING_TRIGGER', 'PENDING', 'ANALYZING']
                ).values_list('wallet_id', flat=True)
            )
            for wid in pending_wids:
                w = WalletAccount.objects.filter(pk=wid).first()
                if w:
                    n += cls.purge_pending_if_at_cap(w)
            return n
        if not cls.wallet_at_position_cap(wallet):
            return 0
        return cls.purge_pending_plans(wallet)

    @classmethod
    def purge_pending_plans(cls, wallet: WalletAccount | None = None, symbol: str | None = None) -> int:
        """Xóa plan đang chờ (chưa khớp). Không giữ hàng đợi lệch hướng."""
        qs = TradingPlan.objects.filter(status__in=['PENDING_TRIGGER', 'PENDING', 'ANALYZING'])
        if wallet is not None:
            qs = qs.filter(wallet=wallet)
        if symbol:
            qs = qs.filter(symbol=symbol)
        n = qs.count()
        if n:
            qs.delete()
        return n

    @classmethod
    def purge_all_plans_for_wallet(cls, wallet: WalletAccount) -> int:
        qs = TradingPlan.objects.filter(wallet=wallet)
        n = qs.count()
        if n:
            qs.delete()
        return n

    @staticmethod
    def direction_from_forecast(forecast) -> str:
        """Kế hoạch AI chỉ BUY hoặc SELL — không WAIT / không vùng entry."""
        bias = str(getattr(forecast, 'trend_bias', '') or '').upper()
        if bias == 'BEARISH':
            return 'SELL'
        if bias == 'BULLISH':
            return 'BUY'
        action = str(getattr(forecast, 'recommended_action', '') or '').upper()
        if 'SELL' in action:
            return 'SELL'
        return 'BUY'

    @staticmethod
    def generate_plan_for_wallet(wallet: WalletAccount, symbol_config: SymbolConfig, forecast: MarketForecast, is_pyramiding: bool = False) -> TradingPlan:
        # Check if symbol is allowed for this wallet
        if symbol_config.symbol not in wallet.allowed_symbols:
            return None
        if not getattr(wallet, 'is_active', False):
            AutoPlanGenerator.purge_all_plans_for_wallet(wallet)
            return None
        if not AutoPlanGenerator.wallet_has_capital(wallet):
            AutoPlanGenerator.purge_all_plans_for_wallet(wallet)
            return None
        if AutoPlanGenerator.wallet_at_position_cap(wallet):
            AutoPlanGenerator.purge_pending_plans(wallet)
            return None
        fields = AutoPlanGenerator._plan_fields(wallet, symbol_config, forecast, is_pyramiding=is_pyramiding)
        if not fields:
            return None
        fields['created_at'] = timezone.now()
        return TradingPlan.objects.create(**fields)

    @staticmethod
    def _plan_fields(wallet: WalletAccount, symbol_config: SymbolConfig, forecast: MarketForecast, is_pyramiding: bool = False) -> dict:

        # 1. Chỉ BUY hoặc SELL — vào MARKET ngay, không chờ hồi / vùng entry
        direction = AutoPlanGenerator.direction_from_forecast(forecast)

        try:
            symbol_config.refresh_from_db()
        except Exception:
            pass

        # Luôn khớp giá MARKET (Ask khi BUY, Bid khi SELL) tại thời điểm lập kế hoạch
        entry_price = AutoPlanGenerator.market_entry_price(symbol_config, direction)

        lot = float(wallet.default_lot_size or 0.01)
        lot = max(0.01, round(lot, 2))
        min_tp = float(wallet.min_take_profit_usd or 0) if wallet.min_take_profit_usd else 0.0
        max_sl = float(wallet.max_stop_loss_usd or 0) if getattr(wallet, 'max_stop_loss_usd', None) else 0.0
        trail_on = bool(getattr(wallet, 'trail_sl_enabled', False))
        trail_lock = float(getattr(wallet, 'trail_sl_lock_usd', 0) or 0) if trail_on else 0.0

        tag_name = "AI Lướt Sóng Ngắn Hạn (Scale-In)" if is_pyramiding else "AI Lướt Sóng Ngắn Hạn"
        max_n = int(wallet.max_open_trades or 1)
        tp_txt = f"Chốt lời khi lãi ròng >= ${min_tp:.2f}." if min_tp > 0 else "Không tự chốt lời (min TP trống)."
        sl_txt = f"Cắt lỗ khi lỗ ròng <= -${max_sl:.2f} (SL_HIT)." if max_sl > 0 else "Không tự cắt lỗ USD (max SL trống)."
        trail_txt = (
            f" Tự dời SL: khoá ${trail_lock:.2f} (lãi 2× → SL còn 1×; chỉ dời tăng)."
            if trail_on and trail_lock > 0 else ""
        )
        rationale = (
            f"{tag_name}: MARKET {direction} {lot} Lot ngay (Ask khi BUY / Bid khi SELL), không chờ vùng entry. "
            f"{tp_txt} {sl_txt}{trail_txt} "
            f"Cho phép tối đa {max_n} lệnh mở đồng thời trên ví; còn slot thì bot lập plan mới và khớp ngay."
        )

        fields = {
            'wallet': wallet,
            'symbol': symbol_config.symbol,
            'timeframe': symbol_config.timeframe,
            'direction': direction,
            'entry_price': entry_price,
            'entry_zone_low': entry_price,
            'entry_zone_high': entry_price,
            'stop_loss': None,
            'take_profit_1': None,
            'take_profit_2': None,
            'rr_ratio': 1.0,
            'calculated_lot': lot,
            'risk_amount_usd': Decimal('0.00'),
            'rationale': rationale,
            'status': 'PENDING_TRIGGER',
        }
        return fields

    @staticmethod
    def market_entry_price(symbol_config: SymbolConfig, direction: str) -> Decimal:
        """Giá vào lệnh MARKET: Ask khi BUY, Bid khi SELL."""
        digits = int(getattr(symbol_config, 'digits', 5) or 5)
        if direction == 'BUY':
            raw = symbol_config.current_ask or symbol_config.current_price
        else:
            raw = symbol_config.current_bid or symbol_config.current_price
        if raw is None:
            return Decimal('0')
        return Decimal(str(round(float(raw), digits)))

    @classmethod
    def refresh_symbol_market_price(cls, symbol_name: str) -> SymbolConfig:
        """Lấy lại giá bid/ask mới nhất trước khi lập plan / khớp lệnh."""
        try:
            from apps.trading.live_market_feed import LiveMarketFeedService
            LiveMarketFeedService.sync_all_symbols()
        except Exception:
            pass
        return SymbolConfig.objects.filter(symbol=symbol_name).first()

    @classmethod
    def update_or_create_plan_for_wallet(cls, wallet: WalletAccount, symbol_config: SymbolConfig, forecast: MarketForecast, is_pyramiding: bool = False) -> TradingPlan:
        """Làm mới 1 plan hiện tại cho ví+cặp: plan cũ (không EXECUTING) bị xóa, không tích dồn."""
        if symbol_config.symbol not in wallet.allowed_symbols:
            return None
        if not getattr(wallet, 'is_active', False):
            cls.purge_all_plans_for_wallet(wallet)
            return None
        if not cls.wallet_has_capital(wallet):
            cls.purge_all_plans_for_wallet(wallet)
            return None
        if cls.wallet_at_position_cap(wallet):
            cls.purge_pending_plans(wallet)
            return None
        try:
            symbol_config.refresh_from_db()
        except Exception:
            pass

        fields = cls._plan_fields(wallet, symbol_config, forecast, is_pyramiding=is_pyramiding)
        if not fields:
            return None

        # Bỏ plan chết + các plan chờ cũ cùng ví/cặp; giữ EXECUTING (đang có lệnh mở)
        TradingPlan.objects.filter(
            wallet=wallet,
            symbol=symbol_config.symbol,
            status__in=['FAILED', 'CANCELLED', 'COMPLETED', 'PENDING', 'ANALYZING'],
        ).delete()

        pending_qs = TradingPlan.objects.filter(
            wallet=wallet,
            symbol=symbol_config.symbol,
            status='PENDING_TRIGGER',
        ).order_by('-updated_at', '-created_at')
        keep = pending_qs.first()
        if keep:
            pending_qs.exclude(pk=keep.pk).delete()
            direction_changed = str(keep.direction or '') != str(fields.get('direction') or '')
            for k, v in fields.items():
                setattr(keep, k, v)
            keep.status = 'PENDING_TRIGGER'
            if direction_changed:
                # Đổi BUY↔SELL = kế hoạch mới, không giữ timestamp/hướng cũ.
                keep.created_at = timezone.now()
            keep.save()
            return keep

        fields['created_at'] = timezone.now()
        return TradingPlan.objects.create(**fields)

    @classmethod
    def discard_plan(cls, plan: TradingPlan) -> None:
        """Bỏ plan thất bại / hết hiệu lực: xóa hẳn khỏi DB."""
        if not plan or not getattr(plan, 'pk', None):
            return
        try:
            plan.delete()
        except Exception:
            pass

    @classmethod
    def purge_dead_plans(cls) -> int:
        """
        Không tích dồn: xóa FAILED/CANCELLED/COMPLETED ngay.
        Ví đã đủ lệnh: xóa hết plan PENDING treo. Chỉ giữ EXECUTING (lệnh đang mở).
        Chưa đủ lệnh: chỉ giữ 1 plan PENDING mới nhất mỗi ví+cặp.
        """
        dead_qs = TradingPlan.objects.filter(status__in=['FAILED', 'CANCELLED', 'COMPLETED'])
        count = dead_qs.count()
        dead_qs.delete()

        cutoff = timezone.now() - timedelta(seconds=STALE_PLAN_SECONDS)
        stale_qs = TradingPlan.objects.filter(
            status__in=['PENDING_TRIGGER', 'PENDING', 'ANALYZING'],
            updated_at__lt=cutoff,
        )
        count += stale_qs.count()
        stale_qs.delete()

        count += cls.purge_wrong_direction_pending()

        pending = list(
            TradingPlan.objects.filter(
                status__in=['PENDING_TRIGGER', 'PENDING', 'ANALYZING']
            ).order_by('wallet_id', 'symbol', '-updated_at', '-created_at')
        )
        seen = set()
        drop_ids = []
        for p in pending:
            key = (p.wallet_id, p.symbol)
            if key in seen:
                drop_ids.append(p.pk)
            else:
                seen.add(key)
        if drop_ids:
            count += len(drop_ids)
            TradingPlan.objects.filter(pk__in=drop_ids).delete()

        count += cls.purge_pending_if_at_cap()
        return count

    @classmethod
    def purge_wrong_direction_pending(cls) -> int:
        """Xóa plan PENDING còn BUY trong khi forecast đã BEARISH (và ngược lại)."""
        pending = list(
            TradingPlan.objects.filter(status__in=['PENDING_TRIGGER', 'PENDING', 'ANALYZING'])
        )
        if not pending:
            return 0
        symbols = {p.symbol for p in pending}
        latest = {}
        for fc in MarketForecast.objects.filter(symbol__in=symbols).order_by('symbol', '-updated_at'):
            latest.setdefault(fc.symbol, fc)
        drop_ids = []
        for p in pending:
            fc = latest.get(p.symbol)
            if not fc:
                continue
            if cls.direction_from_forecast(fc) != p.direction:
                drop_ids.append(p.pk)
        if drop_ids:
            TradingPlan.objects.filter(pk__in=drop_ids).delete()
        return len(drop_ids)

    @classmethod
    def refresh_plans_for_wallet(cls, wallet: WalletAccount) -> list:
        """
        Không lập sẵn plan cho mọi cặp.
        Chỉ xóa plan chờ cũ; plan mới chỉ sinh khi còn slot lệnh (try_immediate / refill).
        """
        if not getattr(wallet, 'is_active', False):
            cls.purge_all_plans_for_wallet(wallet)
            return []
        if not cls.wallet_has_capital(wallet):
            cls.purge_all_plans_for_wallet(wallet)
            return []

        TradingPlan.objects.filter(
            wallet=wallet,
            status__in=['PENDING_TRIGGER', 'PENDING', 'ANALYZING', 'CANCELLED', 'FAILED']
        ).delete()
        return []

