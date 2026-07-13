"""LLM hypothesis generation. Spec §3.1-3.3."""
import sys
from datetime import datetime, timezone
from pathlib import Path

_SYSTEM_PROMPT = """你是量化策略研究员。任务:基于一个量化策略某因子层的复盘归因 + 代际优化历史,
诊断该层"为什么 reward shaping 修不动",并提出一个替代设计假设(新策略思路)。

约束:
- 只针对指定的失效层提假设,不要泛泛给策略 idea
- 假设必须可落地为 A 股策略(518880/600xxx/000xxx/300xxx/ETF;T+1;100 股整手;不可做空)
- 假设要回答:"该层结构性失效的根因是什么" + "替代设计如何避免该根因"
- 不引入外部资料,只基于提供的复盘 + 代际数据推理
- 输出 Markdown,包含:根因诊断、替代设计假设、预期改善、A 股落地映射"""


def build_prompt(strategy_name, inspired_layer, review_doc, gen_history, layer_semantic, threshold):
    gen_table = "\n".join(
        f"  代 {g['generation']} | gap={g.get('layer_gaps',{}).get(inspired_layer,{}).get('gap',0):.2f} | "
        f"weight={g.get('shaping_overrides',{}).get(inspired_layer + '_contrib_penalty', 0):.2f} | "
        f"review_status={g.get('review_status','?')}"
        for g in gen_history
    ) or "  (无代际历史)"
    weights = [g.get("shaping_overrides", {}).get(inspired_layer + "_contrib_penalty", 0) for g in gen_history]
    w1 = weights[0] if weights else 0
    wN = weights[-1] if weights else 0
    layer_agg = review_doc.get("layer_attribution", {}).get(inspired_layer, {})
    neg_trades = [t for t in review_doc.get("per_trade_narrative", [])
                  if float(t.get("layer_contributions", {}).get(inspired_layer, 0)) < 0][:10]
    neg_trades_str = "\n".join(f"  {t.get('entry_time','?')}: {t.get('layer_contributions',{}).get(inspired_layer)}"
                               for t in neg_trades) or "  (无负贡献交易)"
    return f"""## 策略
{strategy_name}

## 失效层
{inspired_layer} (层语义: {layer_semantic})

## 该层代际历史(最近 {len(gen_history)} 代)
{gen_table}
代际观察: gap 连续 {len(gen_history)} 代超阈 {threshold}, shaping weight 从 {w1:.2f} 升到 {wN:.2f} 未收敛。

## 当前 review.json 该层归因细节
layer_attribution[{inspired_layer}]: {layer_agg}

## 该层 per-trade 负贡献样本(最多 10 笔)
{neg_trades_str}

## 输出要求
Markdown 文档,标题: "受 {strategy_name} {inspired_layer} 层失效启发的策略假设",
含 4 节: 根因诊断 / 替代设计假设 / 预期改善 / A 股落地映射。"""


def _call_llm(system_prompt, user_prompt, llm_cfg):
    """Call glm-5.1 via soloquant_orchestrator HTTP client. Spec §3.1."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Scripts"))
    import json as _json
    from soloquant_orchestrator import create_http_client_from_config, default_post_json
    config_path = Path(__file__).resolve().parents[2] / "Launcher" / "config" / "config-soloquant.json"
    config = _json.loads(config_path.read_text()) if config_path.exists() else {}
    client = create_http_client_from_config(config, post_json=default_post_json, use_code_generation_llm=True)
    model = llm_cfg.get("model", "glm-5.1")
    request_payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
        "temperature": llm_cfg.get("temperature", 0.7),
        "max_tokens": 4096,
    }
    response = client(request_payload)
    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
    return str(response)


def run(strategy_name, inspired_layer, review_doc, gen_history, manifest_raw, llm_cfg,
        hypothesis_dir="Results/soloquant/local-strategies"):
    layer_semantic = inspired_layer
    threshold = manifest_raw.get("inspiration", {}).get("persistence", {}).get("gap_threshold", 0.15)
    user_prompt = build_prompt(strategy_name, inspired_layer, review_doc, gen_history, layer_semantic, threshold)
    markdown = _call_llm(_SYSTEM_PROMPT, user_prompt, llm_cfg)
    if len(markdown.strip()) < 100:
        raise ValueError(f"LLM output too short ({len(markdown)} chars < 100), not writing hypothesis file")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    filename = f"{strategy_name}-{inspired_layer}-inspired-{ts}.md"
    out_dir = Path(hypothesis_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    out_path.write_text(markdown)
    return str(out_path)
