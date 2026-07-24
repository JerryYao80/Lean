// file: Common/Risk/VaR/VarConfig.cs
using System;

namespace QuantConnect.Risk.VaR
{
    /// <summary>
    /// Configuration for VaR computation. Use FromScenario to derive
    /// confidence/horizon from a VaRScenario; otherwise HorizonDays/Confidence
    /// are ignored when a scenario is passed to VarEngine.Compute.
    /// </summary>
    public readonly struct VarConfig
    {
        public int LookbackDays { get; }
        public int MinHistoryDays { get; }
        public int HorizonDays { get; }      // ignored when scenario is passed
        public double Confidence { get; }    // ignored when scenario is passed
        public int BootstrapIterations { get; }
        public int MonteCarloPaths { get; }
        public int RandomSeed { get; }
        public bool EnforcePdCovariance { get; }
        public double EigenvalueFloor { get; }
        public bool UseOverlappingFor10Day { get; }
        public bool UseSqrtScalingFor10Day { get; }

        public VarConfig(
            int lookbackDays = VaRConstants.DefaultLookbackDays,
            int minHistoryDays = VaRConstants.MinHistoryDays,
            int horizonDays = 1,
            double confidence = 0.99,
            int bootstrapIterations = VaRConstants.DefaultBootstrapIterations,
            int monteCarloPaths = VaRConstants.DefaultMonteCarloPaths,
            int randomSeed = VaRConstants.FixedSeed,
            bool enforcePdCovariance = true,
            double eigenvalueFloor = VaRConstants.EigenvalueFloor,
            bool useOverlappingFor10Day = true,
            bool useSqrtScalingFor10Day = true)
        {
            LookbackDays = lookbackDays;
            MinHistoryDays = minHistoryDays;
            HorizonDays = horizonDays;
            Confidence = confidence;
            BootstrapIterations = bootstrapIterations;
            MonteCarloPaths = monteCarloPaths;
            RandomSeed = randomSeed;
            EnforcePdCovariance = enforcePdCovariance;
            EigenvalueFloor = eigenvalueFloor;
            UseOverlappingFor10Day = useOverlappingFor10Day;
            UseSqrtScalingFor10Day = useSqrtScalingFor10Day;
        }

        /// <summary>Derive confidence + horizon from a VaRScenario.</summary>
        public static VarConfig FromScenario(VaRScenario scenario, int lookbackDays = VaRConstants.DefaultLookbackDays)
        {
            return scenario switch
            {
                VaRScenario.OneDay95 => new VarConfig(lookbackDays: lookbackDays, horizonDays: 1, confidence: 0.95),
                VaRScenario.OneDay99 => new VarConfig(lookbackDays: lookbackDays, horizonDays: 1, confidence: 0.99),
                VaRScenario.TenDay99 => new VarConfig(lookbackDays: lookbackDays, horizonDays: 10, confidence: 0.99),
                _ => throw new ArgumentException($"Unknown scenario: {scenario}")
            };
        }
    }
}
