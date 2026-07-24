// file: Tests/Algorithm/VarStrategyTests.cs
using NUnit.Framework;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class VarStrategyTests
    {
        [Test]
        public void VarStrategy_Compiles()
        {
            var type = typeof(QuantConnect.Algorithm.CSharp.VarStrategy);
            Assert.IsNotNull(type);
            Assert.AreEqual("VarStrategy", type.Name);
        }
    }
}
