using NUnit.Framework;
using QuantConnect.Algorithm.CSharp.Models.Gold2;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class Gold2VolTargetPortfolioModelTests
    {
        // 设计 §5.3: target = w_smooth × dirCoef; |Δ|<threshold 维持 last
        [Test]
        public void DeadZone_BelowThreshold_HoldsLast()
            => Assert.AreEqual(0.80m, Gold2VolTargetPortfolioModel.ApplyDeadZone(0.80m, 0.82m, 0.05m));

        [Test]
        public void DeadZone_AboveThreshold_Updates()
            => Assert.AreEqual(0.90m, Gold2VolTargetPortfolioModel.ApplyDeadZone(0.80m, 0.90m, 0.05m));
    }
}
