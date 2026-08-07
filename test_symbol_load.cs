using System;
using QuantConnect;
using QuantConnect.Securities;

class TestSymbolLoad
{
    static void Main()
    {
        // Set data folder
        Globals.Reset();
        
        var db = SymbolPropertiesDatabase.FromDataFolder();
        
        // Test SSE
        var sseProps = db.GetSymbolProperties("sse", null, SecurityType.Equity, "CNY");
        Console.WriteLine($"SSE PriceMagnifier: {sseProps.PriceMagnifier}");
        
        // Test SZSE
        var szseProps = db.GetSymbolProperties("szse", null, SecurityType.Equity, "CNY");
        Console.WriteLine($"SZSE PriceMagnifier: {szseProps.PriceMagnifier}");
    }
}
