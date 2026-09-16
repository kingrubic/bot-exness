//+------------------------------------------------------------------+
//|                                       MT5_Command_Bridge.mq5     |
//|                                  Copyright 2026, Antigravity AI  |
//+------------------------------------------------------------------+
#property copyright "Antigravity AI"
#property link      "https://github.com/anhmanh12/exness"
#property version   "2.00"
#property description "Native MQL5 Execution Bridge for Exness"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

CTrade m_trade;
CPositionInfo m_pos;

int OnInit()
{
   m_trade.SetExpertMagicNumber(8882026);
   m_trade.SetTypeFilling(ORDER_FILLING_FOK);
   m_trade.SetDeviationInPoints(100);
   EventSetMillisecondTimer(50);
   Print("✅ MT5 Native Command Bridge Initialized Successfully!");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTick()
{
   CheckAndProcessCommands();
}

void OnTimer()
{
   CheckAndProcessCommands();
}

void CheckAndProcessCommands()
{
   if(!FileIsExist("commands.txt", FILE_COMMON))
      return;

   int file_handle = FileOpen("commands.txt", FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(file_handle == INVALID_HANDLE)
      return;

   string cmd_line = FileReadString(file_handle);
   FileClose(file_handle);
   FileDelete("commands.txt", FILE_COMMON);

   if(StringLen(cmd_line) == 0)
      return;

   Print("📥 Native Bridge Received Command: ", cmd_line);

   string parts[];
   int count = StringSplit(cmd_line, '|', parts);
   if(count < 2)
      return;

   string action = parts[0];
   bool success = false;
   string res_msg = "";
   ulong deal_ticket = 0;
   double exec_price = 0;
   ulong result_ticket = 0;

   if(action == "CLOSE")
   {
      ulong ticket = (ulong)StringToInteger(parts[1]);
      string sym = (count > 2 && StringLen(parts[2]) > 0) ? parts[2] : "";
      
      // Try closing position
      if(m_trade.PositionClose(ticket, 100))
      {
         success = true;
         deal_ticket = m_trade.ResultDeal();
         exec_price = m_trade.ResultPrice();
         result_ticket = m_trade.ResultOrder();
         res_msg = "Đã đóng thành công lệnh #" + (string)ticket;
         Print("✅ Closed position #", ticket, " Deal: #", deal_ticket, " Price: ", exec_price);
      }
      else
      {
         // Try finding position by ticket and close with symbol
         if(PositionSelectByTicket(ticket))
         {
            string p_sym = PositionGetString(POSITION_SYMBOL);
            if(m_trade.PositionClose(ticket, 100))
            {
               success = true;
               deal_ticket = m_trade.ResultDeal();
               exec_price = m_trade.ResultPrice();
               res_msg = "Đã đóng thành công lệnh #" + (string)ticket;
            }
            else
            {
               res_msg = "Close failed: " + m_trade.ResultRetcodeDescription();
            }
         }
         else
         {
            // Position already closed
            success = true;
            res_msg = "Vị thế #" + (string)ticket + " đã đóng trước đó";
         }
      }
   }
   else if(action == "BUY")
   {
      string symbol = parts[1];
      double volume = StringToDouble(parts[2]);
      double sl = (count > 3) ? StringToDouble(parts[3]) : 0;
      double tp = (count > 4) ? StringToDouble(parts[4]) : 0;
      string comment = (count > 6) ? parts[6] : "Web Auto Trade";

      if(m_trade.Buy(volume, symbol, 0, sl, tp, comment))
      {
         success = true;
         deal_ticket = m_trade.ResultDeal();
         exec_price = m_trade.ResultPrice();
         result_ticket = m_trade.ResultOrder();
         res_msg = "Mở lệnh BUY thành công #" + (string)result_ticket;
      }
      else
      {
         res_msg = "Buy failed: " + m_trade.ResultRetcodeDescription();
      }
   }
   else if(action == "SELL")
   {
      string symbol = parts[1];
      double volume = StringToDouble(parts[2]);
      double sl = (count > 3) ? StringToDouble(parts[3]) : 0;
      double tp = (count > 4) ? StringToDouble(parts[4]) : 0;
      string comment = (count > 6) ? parts[6] : "Web Auto Trade";

      if(m_trade.Sell(volume, symbol, 0, sl, tp, comment))
      {
         success = true;
         deal_ticket = m_trade.ResultDeal();
         exec_price = m_trade.ResultPrice();
         result_ticket = m_trade.ResultOrder();
         res_msg = "Mở lệnh SELL thành công #" + (string)result_ticket;
      }
      else
      {
         res_msg = "Sell failed: " + m_trade.ResultRetcodeDescription();
      }
   }
   else if(action == "MODIFY")
   {
      ulong ticket = (ulong)StringToInteger(parts[1]);
      double sl = (count > 2) ? StringToDouble(parts[2]) : 0;
      double tp = (count > 3) ? StringToDouble(parts[3]) : 0;
      if(m_trade.PositionModify(ticket, sl, tp))
      {
         success = true;
         result_ticket = ticket;
         res_msg = "Đã dời SL/TP thành công lệnh #" + (string)ticket;
      }
      else
      {
         res_msg = "Modify failed: " + m_trade.ResultRetcodeDescription();
      }
   }

   int out_handle = FileOpen("responses.txt", FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(out_handle != INVALID_HANDLE)
   {
      string resp = (success ? "OK" : "ERROR") + "|" + res_msg + "|" + (string)deal_ticket + "|" + DoubleToString(exec_price, 5) + "|" + (string)result_ticket;
      FileWriteString(out_handle, resp);
      FileClose(out_handle);
   }
}
