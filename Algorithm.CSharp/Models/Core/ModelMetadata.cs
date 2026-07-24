using System.Collections.Generic;

namespace QuantConnect.Algorithm.CSharp.Models.Core
{
    /// <summary>模型动物园元数据 - 用于 ModelRegistry 检索</summary>
    public class ModelMetadata
    {
        public string Id { get; set; }
        public string Name { get; set; }
        public string ModelType { get; set; }
        public string Description { get; set; }
        public List<string> Dependencies { get; set; }
    }
}
