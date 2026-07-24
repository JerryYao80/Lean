using System.Collections.Generic;

namespace QuantConnect.Factors.Core
{
    public class FactorMetadata
    {
        public string Id { get; set; }
        public string Name { get; set; }
        public FactorCategory Category { get; set; }
        public FactorScope Scope { get; set; }
        public FactorComputeMode ComputeMode { get; set; }
        public string DataSource { get; set; }
        public Dictionary<string, object> Parameters { get; set; }
        public List<string> Dependencies { get; set; }
        public string Description { get; set; }
        public string CsvPath { get; set; }
    }
}