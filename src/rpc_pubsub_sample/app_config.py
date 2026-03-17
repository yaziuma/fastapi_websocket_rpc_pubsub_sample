from __future__ import annotations

from pathlib import Path
import tomllib

from .node_app import NodeConfig


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.toml"


def load_settings() -> dict:
    with CONFIG_PATH.open("rb") as file:
        return tomllib.load(file)


def load_node_config(section_name: str) -> NodeConfig:
    settings = load_settings()
    current = settings[section_name]

    return NodeConfig(
        name=current["name"],
        host=current["host"],
        port=current["port"],
        outbound_token=current["outbound_token"],
        accepted_tokens=current["accepted_tokens"],
    )
