"""trigger.detect: continuous N-gen non-convergence + ceiling判据. Spec §2.4, spirit2 #1.
注意:平铺导入风格(sys.path.insert Scripts/inspiration 后 from trigger import detect),
不用相对导入。detect() 只用 ls.status 鸭子类型,不需导入 LayerState 类型(spirit3 #2)。"""


def detect(strategy: str, last_n_gens: list, layer_states: dict, thresholds: dict,
           ceiling: float = 3.0, eps: float = 0.01) -> list:
    """Return layer names that should enter inspiration_pending.

    For each status==optimizing layer: continuous N gens gap>threshold AND
    weight non-convergent (trend up OR fluctuate OR ceiling-pinned).
    """
    min_gens = thresholds.get("min_generations", 3)
    gap_thresh = thresholds.get("gap_threshold", 0.15)
    delta_thresh = thresholds.get("weight_nonconvergence_delta", 0.1)
    if len(last_n_gens) < min_gens:
        return []

    all_layers = set()
    for gen in last_n_gens:
        all_layers.update(gen.get("layer_gaps", {}).keys())

    to_inspire = []
    for layer in all_layers:
        ls = layer_states.get(layer)
        if ls and ls.status != "optimizing":
            continue

        gaps, weights = [], []
        for gen in last_n_gens:
            g = gen.get("layer_gaps", {}).get(layer, {}).get("gap", 0)
            gaps.append(g)
            w = 0.0
            for term, wv in gen.get("shaping_overrides", {}).items():
                if term.startswith(layer):
                    w = wv
                    break
            weights.append(w)

        if len(gaps) < min_gens or not all(g > gap_thresh for g in gaps):
            continue
        non_convergent = False
        if len(weights) >= 2:
            non_convergent = (weights[-1] - weights[0] > delta_thresh)
            non_convergent = non_convergent or (max(weights) - min(weights) > delta_thresh)
        at_ceiling = all(abs(w - ceiling) < eps for w in weights) if weights else False
        non_convergent = non_convergent or at_ceiling

        if non_convergent:
            to_inspire.append(layer)
    return to_inspire
