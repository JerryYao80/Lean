// file: Common/Risk/VaR/VaRDataQuality.cs
namespace QuantConnect.Risk.VaR
{
    /// <summary>Data quality flags for VaR results.</summary>
    public enum VaRDataQuality
    {
        Valid,
        InsufficientHistory,
        ZeroVariance,
        NonPdCovariance,
        DegenerateTail,
        AllReturnsConstant
    }
}
