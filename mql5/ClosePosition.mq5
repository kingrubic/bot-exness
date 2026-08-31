//+------------------------------------------------------------------+
//|                                                ClosePosition.mq5 |
//|                                  Copyright 2026, Antigravity AI  |
//+------------------------------------------------------------------+
#property copyright "Antigravity AI"
#property version   "1.00"
#property script_show_inputs false

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

void OnStart()
{
   CTrade trade;
   trade.SetExpertMagicNumber(8882026);
   trade.SetDeviationInPoints(100);
   trade.SetTypeFilling(ORDER_FILLING_FOK);

   // Read command from Common/Files/commands.txt
   if(!FileIsExist("commands.txt", FILE_COMMON))
   {
      Print("No commands.txt found in FILE_COMMON");
      return;
   }

   int h = FileOpen("commands.txt", FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
   {
      Print("Failed to open commands.txt");
      return;
   }

   string line = FileReadString(h);
   FileClose(h);
   FileDelete("commands.txt", FILE_COMMON);

   Print("Running MQL5 Script with command: ", line);

   string parts[];
   int n = StringSplit(line, '|', parts);
   if(n < 2) return;

   string action = parts[0];
   bool ok = false;
   string msg = "";
   ulong deal = 0;
   double price = 0;

   if(action == "CLOSE")
   {
      ulong ticket = (ulong)StringToInteger(parts[1]);
      if(trade.PositionClose(ticket, 100))
      {
         ok = true;
         deal = trade.ResultDeal();
         price = trade.ResultPrice();
         msg = "Closed #" + (string)ticket;
         Print("✅ [MQL5 SCRIPT] Successfully closed position #", ticket, " deal: #", deal, " price: ", price);
      }
      else
      {
         msg = trade.ResultRetcodeDescription();
         Print("❌ [MQL5 SCRIPT] Close failed: ", msg, " retcode: ", trade.ResultRetcode());
      }
   }
   else if(action == "CLOSE_ALL")
   {
      int total = PositionsTotal();
      int closed_count = 0;
      for(int i = total - 1; i >= 0; i--)
      {
         ulong t = PositionGetTicket(i);
         if(t > 0)
         {
            if(trade.PositionClose(t, 100))
               closed_count++;
         }
      }
      ok = true;
      msg = "Closed " + (string)closed_count + " positions";
   }

   // Write response
   int out = FileOpen("responses.txt", FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(out != INVALID_HANDLE)
   {
      FileWriteString(out, (ok ? "OK|" : "ERROR|") + msg + "|" + (string)deal + "|" + DoubleToString(price, 5));
      FileClose(out);
   }
}
