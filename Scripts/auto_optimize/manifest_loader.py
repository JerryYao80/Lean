"""Strategy manifest loader. Spec §2.1."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import yaml


@dataclass
class ParamSpec:
    name: str
    type: str
    range: tuple
    default: Any
    layer: str
    log: bool = False


@dataclass
class StateField:
    name: str
    type: str
    item_schema: list = None


@dataclass
class StateSchema:
    fields: list
    dim_hint: int = 0


@dataclass
class ShapingTerm:
    term: str
    weight: float


@dataclass
class RewardConfig:
    primary: str
    shaping: list = field(default_factory=list)


@dataclass
class Universe:
    symbols: list
    timezone: str


@dataclass
class StrategyManifest:
    strategy_name: str
    lean_config: str
    risk_model_target: str
    parameter_space: list
    state_schema: StateSchema
    reward_config: RewardConfig
    universe: Universe
    cpcv: dict = None
    walk_forward: dict = None
    ppo_training: dict = None
    rl_state_completeness: str = "unaudited"
    raw: dict = None


def load_manifest(path) -> StrategyManifest:
    raw = yaml.safe_load(Path(path).read_text())
    if not raw or "strategy_name" not in raw:
        raise ValueError("manifest missing strategy_name")
    ps = [ParamSpec(
        name=p["name"], type=p["type"],
        range=tuple(p["range"]), default=p["default"],
        layer=p.get("layer", ""), log=p.get("log", False),
    ) for p in raw.get("parameter_space", [])]
    ss_raw = raw.get("state_schema", {})
    ss = StateSchema(
        fields=[StateField(f["name"], f["type"], f.get("item_schema")) for f in ss_raw.get("fields", [])],
        dim_hint=ss_raw.get("dim_hint", 0),
    )
    rc_raw = raw.get("reward_config", {})
    rc = RewardConfig(
        primary=rc_raw.get("primary", "dsr"),
        shaping=[ShapingTerm(t["term"], t["weight"]) for t in rc_raw.get("shaping", [])],
    )
    u_raw = raw.get("universe", {})
    return StrategyManifest(
        strategy_name=raw["strategy_name"],
        lean_config=raw["lean_config"],
        risk_model_target=raw.get("risk_model_target", ""),
        parameter_space=ps, state_schema=ss, reward_config=rc,
        universe=Universe(u_raw.get("symbols", []), u_raw.get("timezone", "Asia/Shanghai")),
        cpcv=raw.get("cpcv"), walk_forward=raw.get("walk_forward"),
        ppo_training=raw.get("ppo_training"),
        rl_state_completeness=raw.get("rl_state_completeness", "unaudited"),
        raw=raw,
    )
