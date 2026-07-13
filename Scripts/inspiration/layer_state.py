"""LayerState + state machine + persistence. Spec §1.2, §2.3."""
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

_LEGAL_TRANSITIONS = {
    ("optimizing", "inspiration_pending"),
    ("inspiration_pending", "redesigned"),
    ("inspiration_pending", "optimizing"),
    ("redesigned", "inspiration_pending"),
}


@dataclass
class LayerState:
    layer: str
    status: str = "optimizing"
    pending_since_generation: int = -1
    inspired_strategy_id: str = ""
    retired_shaping_terms: list = field(default_factory=list)
    candidate_deployed: bool = False
    candidate_status: str = "none"


def transition(states: dict, layer: str, new_status: str, **kwargs):
    ls = states.get(layer)
    if ls is None:
        ls = LayerState(layer=layer)
        states[layer] = ls
    if (ls.status, new_status) not in _LEGAL_TRANSITIONS:
        raise ValueError(f"illegal transition: {ls.status} → {new_status} for layer {layer}")
    ls.status = new_status
    if new_status == "optimizing":
        ls.pending_since_generation = -1
        ls.inspired_strategy_id = ""
    for k, v in kwargs.items():
        if hasattr(ls, k):
            setattr(ls, k, v)


def load_layer_states(state_path: str, strategy: str) -> dict:
    p = Path(state_path)
    if not p.exists():
        return {}
    try:
        state = json.loads(p.read_text())
    except Exception:
        return {}
    raw = state.get("layer_states", {}).get(strategy, {})
    return {layer: LayerState(**data) for layer, data in raw.items()}


def save_layer_states(state_path: str, strategy: str, states: dict):
    p = Path(state_path)
    state = {}
    if p.exists():
        try:
            state = json.loads(p.read_text())
        except Exception:
            state = {}
    state.setdefault("layer_states", {})[strategy] = {layer: asdict(ls) for layer, ls in states.items()}
    p.write_text(json.dumps(state, indent=2, default=str))
