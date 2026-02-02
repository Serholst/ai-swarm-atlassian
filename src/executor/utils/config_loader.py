"""Configuration loader."""

import yaml
from pathlib import Path
from pydantic import BaseModel
from typing import Any


class SDLCConfig(BaseModel):
    """SDLC configuration model."""

    confluence: dict[str, Any]
    jira: dict[str, Any]
    github: dict[str, Any]
    naming: dict[str, Any]
    operational: dict[str, Any]
    gates: dict[str, Any]
    error_handling: dict[str, Any]
    output: dict[str, Any]
    agent: dict[str, Any]


def load_config(config_path: str | Path = "config/sdlc_config.yaml") -> SDLCConfig:
    """
    Load SDLC configuration from YAML file.

    Args:
        config_path: Path to config file

    Returns:
        Loaded configuration

    Raises:
        FileNotFoundError: If config file not found
        yaml.YAMLError: If config file is invalid
    """
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)

    return SDLCConfig(**config_data)
