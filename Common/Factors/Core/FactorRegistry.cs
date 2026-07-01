using System.Collections.Generic;
using System.Linq;

namespace QuantConnect.Factors.Core
{
    public static class FactorRegistry
    {
        private static readonly Dictionary<string, IFactor> _factors = new();
        private static readonly Dictionary<string, FactorMetadata> _metadata = new();
        private static bool _initialized = false;
        private static readonly object _lock = new();

        public static void Initialize()
        {
            lock (_lock) { if (_initialized) return; _initialized = true; }
        }

        public static void Register(IFactor factor)
        {
            EnsureInitialized();
            _factors[factor.Id] = factor;
            _metadata[factor.Id] = new FactorMetadata
            {
                Id = factor.Id, Name = factor.Name, Category = factor.Category,
                Scope = factor.Scope, ComputeMode = factor.ComputeMode, DataSource = factor.DataSource
            };
        }

        public static IFactor Get(string factorId)
        { EnsureInitialized(); return _factors.TryGetValue(factorId, out var f) ? f : null; }

        public static IReadOnlyList<IFactor> GetByCategory(FactorCategory category)
        { EnsureInitialized(); return _factors.Values.Where(f => f.Category == category).ToList(); }

        public static IReadOnlyList<IFactor> GetByScope(FactorScope scope)
        { EnsureInitialized(); return _factors.Values.Where(f => f.Scope == scope || f.Scope == FactorScope.Both).ToList(); }

        public static IReadOnlyList<IFactor> GetTimingFactors() => GetByScope(FactorScope.TimeSeries);
        public static IReadOnlyList<IFactor> GetSelectionFactors() => GetByScope(FactorScope.CrossSection);
        public static IReadOnlyList<IFactor> GetByComputeMode(FactorComputeMode mode)
        { EnsureInitialized(); return _factors.Values.Where(f => f.ComputeMode == mode).ToList(); }

        public static IReadOnlyDictionary<string, FactorMetadata> AllMetadata()
        { EnsureInitialized(); return _metadata; }

        private static void EnsureInitialized() { if (!_initialized) Initialize(); }
    }
}