# Strategy Manifest

每个接入策略在 `strategies/{strategy_name}/manifest.yaml` 声明其参数空间、状态 schema、reward 配置。

## Schema 字段
- `strategy_name`: LEAN algorithm-type-name
- `lean_config`: Launcher/config/config-{name}.json 相对路径
- `risk_model_target`: 默认风控模型类名（被 RlRiskModel 替换）
- `parameter_space`: Layer A 超参列表，每项 name/type/range/default/layer/log
- `state_schema`: Layer C 状态字段（fields + dim_hint）
- `reward_config`: primary + shaping 项
- `universe`: symbols + timezone
- `cpcv`/`walk_forward`/`ppo_training`: 可选，覆盖全局 config.yaml

## 新策略接入步骤
1. 复制一份 manifest 到 strategies/{new_name}/
2. C# 策略实现 IOptimizableStrategy + IRlStateExportable
3. 跑 `python manifest_lint.py --strategy {new_name}` 通过门 0
