// file: Common/Risk/VaR/VaRScenario.cs
namespace QuantConnect.Risk.VaR
{
    /// <summary>VaR scenarios (confidence + horizon).</summary>
    public enum VaRScenario
    {
        OneDay95,
        OneDay99,
        TenDay99
    }
}
