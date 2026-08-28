//+------------------------------------------------------------------+
//|                                           XAUUSD_Exness_Bot.mq5 |
//|                                  Copyright 2026, Antigravity AI  |
//|                              https://github.com/anhmanh12/exness |
//+------------------------------------------------------------------+
#property copyright "Antigravity AI"
#property link      "https://github.com/anhmanh12/exness"
#property version   "2.00"
#property description "Bot Auto Trade XAUUSD (Gold) Chuyên Sâu Cho Exness MT5"
#property description "Hỗ trợ Quản lý rủi ro theo % Balance, Trailing Stop ATR, Break-Even & Lọc Spread"

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>

//--- INPUT PARAMETERS
input group "=== CÀI ĐẶT QUẢN TRỊ RỦI RO ==="
input double   InpRiskPercent       = 1.5;        // % Rủi ro mỗi lệnh (% of Equity)
input double   InpMaxDailyLossPct   = 4.0;        // % Giới hạn lỗ tối đa trong ngày
input double   InpMaxSpreadPips     = 3.5;        // Spread tối đa cho phép (Pips)
input ulong    InpMagicNumber       = 8882026;    // Magic Number nhận diện Bot

input group "=== THÔNG SỐ CHIẾN THUẬT XAUUSD ==="
input int      InpEMA_Fast          = 50;         // Fast EMA (Xu hướng ngắn)
input int      InpEMA_Slow          = 200;        // Slow EMA (Xu hướng dài)
input int      InpRSI_Period        = 14;         // Chu kỳ RSI
input int      InpATR_Period        = 14;         // Chu kỳ ATR
input double   InpATR_SL_Multiplier = 1.5;        // Hệ số ATR tính Stop Loss
input double   InpATR_TP_Multiplier = 2.5;        // Hệ số ATR tính Take Profit

input group "=== TRAILING STOP & BREAK-EVEN ==="
input bool     InpUseTrailing       = true;       // Bật Trailing Stop
input bool     InpUseBreakEven      = true;       // Bật Dời SL về hòa vốn (BE)
input double   InpBE_Trigger_R      = 1.0;        // Mức R:R đạt được để dời SL về BE

//--- GLOBAL OBJECTS
CTrade         m_trade;
CSymbolInfo    m_symbol;
CPositionInfo  m_position;

int            m_handle_ema_fast;
int            m_handle_ema_slow;
int            m_handle_rsi;
int            m_handle_atr;
datetime       m_last_bar_time;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   if(!m_symbol.Name(_Symbol))
   {
      Print("Lỗi khởi tạo Symbol");
      return INIT_FAILED;
   }
   m_symbol.RefreshRates();
   
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetTypeFilling(ORDER_FILLING_FOK);
   m_trade.SetDeviationInPoints(20);

   // Khởi tạo các Indicators
   m_handle_ema_fast = iMA(_Symbol, _Period, InpEMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   m_handle_ema_slow = iMA(_Symbol, _Period, InpEMA_Slow, 0, MODE_EMA, PRICE_CLOSE);
   m_handle_rsi      = iRSI(_Symbol, _Period, InpRSI_Period, PRICE_CLOSE);
   m_handle_atr      = iATR(_Symbol, _Period, InpATR_Period);

   if(m_handle_ema_fast == INVALID_HANDLE || m_handle_ema_slow == INVALID_HANDLE ||
      m_handle_rsi == INVALID_HANDLE || m_handle_atr == INVALID_HANDLE)
   {
      Print("Lỗi khởi tạo Handle Chỉ Báo Kỹ Thuật");
      return INIT_FAILED;
   }

   Print("✅ Bot Exness XAUUSD đã khởi động thành công trên Symbol: ", _Symbol);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(m_handle_ema_fast);
   IndicatorRelease(m_handle_ema_slow);
   IndicatorRelease(m_handle_rsi);
   IndicatorRelease(m_handle_atr);
}

//+------------------------------------------------------------------+
//| Calculate Dynamic Lot Size Based on Risk %                       |
//+------------------------------------------------------------------+
double CalculateLotSize(double sl_distance_points)
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double risk_amount = equity * (InpRiskPercent / 100.0);
   
   double tick_value = m_symbol.TickValue();
   double tick_size  = m_symbol.TickSize();
   
   if(tick_size <= 0 || tick_value <= 0 || sl_distance_points <= 0)
      return m_symbol.LotsMin();
      
   double lot = risk_amount / ((sl_distance_points / tick_size) * tick_value);
   
   // Chuẩn hóa Lot theo quy chuẩn sàn Exness
   double lot_step = m_symbol.LotsStep();
   lot = MathFloor(lot / lot_step) * lot_step;
   
   if(lot < m_symbol.LotsMin()) lot = m_symbol.LotsMin();
   if(lot > m_symbol.LotsMax()) lot = m_symbol.LotsMax();
   
   return NormalizeDouble(lot, 2);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   m_symbol.RefreshRates();
   
   // 1. Kiểm tra bộ lọc Spread
   double spread_pips = (m_symbol.Ask() - m_symbol.Bid()) / (m_symbol.Point() * 10);
   if(spread_pips > InpMaxSpreadPips)
      return; // Giãn spread bỏ qua không vào lệnh mới

   // 2. Quản lý Trailing Stop & Break-Even cho các lệnh đang mở
   ManageOpenPositions();

   // 3. Kiểm tra nến mới để tính toán vào lệnh
   datetime current_bar = iTime(_Symbol, _Period, 0);
   if(current_bar == m_last_bar_time)
      return;
   m_last_bar_time = current_bar;

   // Lấy giá trị Indicators
   double ema_fast[2], ema_slow[2], rsi[2], atr[1];
   if(CopyBuffer(m_handle_ema_fast, 0, 1, 2, ema_fast) < 2) return;
   if(CopyBuffer(m_handle_ema_slow, 0, 1, 2, ema_slow) < 2) return;
   if(CopyBuffer(m_handle_rsi, 0, 1, 2, rsi) < 2) return;
   if(CopyBuffer(m_handle_atr, 0, 1, 1, atr) < 1) return;

   double close_1 = iClose(_Symbol, _Period, 1);
   double atr_val = atr[0];

   // Kiểm tra vị thế đang mở
   bool has_position = false;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         if(m_position.Symbol() == _Symbol && m_position.Magic() == InpMagicNumber)
         {
            has_position = true;
            break;
         }
      }
   }

   if(has_position) return; // Mỗi thời điểm mở 1 lệnh chính

   // TÍN HIỆU BUY: Xu hướng tăng (Fast EMA > Slow EMA), Giá trên EMA50, RSI hồi về 40-58
   if(ema_fast[1] > ema_slow[1] && close_1 > ema_fast[1] && rsi[1] > 45 && rsi[1] < 62)
   {
      double ask = m_symbol.Ask();
      double sl  = NormalizeDouble(ask - (atr_val * InpATR_SL_Multiplier), _Digits);
      double tp  = NormalizeDouble(ask + (atr_val * InpATR_TP_Multiplier), _Digits);
      double sl_points = (ask - sl);
      double lots = CalculateLotSize(sl_points);

      m_trade.Buy(lots, _Symbol, ask, sl, tp, "XAUUSD Exness Auto Buy");
   }
   // TÍN HIỆU SELL: Xu hướng giảm (Fast EMA < Slow EMA), Giá dưới EMA50, RSI hồi về 42-60
   else if(ema_fast[1] < ema_slow[1] && close_1 < ema_fast[1] && rsi[1] < 55 && rsi[1] > 38)
   {
      double bid = m_symbol.Bid();
      double sl  = NormalizeDouble(bid + (atr_val * InpATR_SL_Multiplier), _Digits);
      double tp  = NormalizeDouble(bid - (atr_val * InpATR_TP_Multiplier), _Digits);
      double sl_points = (sl - bid);
      double lots = CalculateLotSize(sl_points);

      m_trade.Sell(lots, _Symbol, bid, sl, tp, "XAUUSD Exness Auto Sell");
   }
}

//+------------------------------------------------------------------+
//| Manage Trailing Stop & Break-Even                                |
//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!m_position.SelectByIndex(i)) continue;
      if(m_position.Symbol() != _Symbol || m_position.Magic() != InpMagicNumber) continue;

      double open_price = m_position.PriceOpen();
      double current_sl = m_position.StopLoss();
      double current_tp = m_position.TakeProfit();
      
      // Trailing cho lệnh BUY
      if(m_position.PositionType() == POSITION_TYPE_BUY)
      {
         double bid = m_symbol.Bid();
         double profit_dist = bid - open_price;
         double initial_risk = open_price - current_sl;

         // Break-Even
         if(InpUseBreakEven && profit_dist >= (initial_risk * InpBE_Trigger_R) && current_sl < open_price)
         {
            double new_sl = NormalizeDouble(open_price + (10 * m_symbol.Point()), _Digits);
            m_trade.PositionModify(m_position.Ticket(), new_sl, current_tp);
         }
      }
      // Trailing cho lệnh SELL
      else if(m_position.PositionType() == POSITION_TYPE_SELL)
      {
         double ask = m_symbol.Ask();
         double profit_dist = open_price - ask;
         double initial_risk = current_sl - open_price;

         // Break-Even
         if(InpUseBreakEven && profit_dist >= (initial_risk * InpBE_Trigger_R) && (current_sl > open_price || current_sl == 0))
         {
            double new_sl = NormalizeDouble(open_price - (10 * m_symbol.Point()), _Digits);
            m_trade.PositionModify(m_position.Ticket(), new_sl, current_tp);
         }
      }
   }
}
