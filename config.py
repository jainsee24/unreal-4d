"""Configuration for Instant4D — Unreal Engine 5 3D Scene Generator."""

import os
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.resolve()
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
RENDERS_DIR = PROJECT_ROOT / "renders"


@dataclass
class PipelineConfig:
    ue_host: str = "localhost"
    ue_port: int = 8000
    workspace_dir: Path = WORKSPACE_DIR
    renders_dir: Path = RENDERS_DIR
    image_width: int = 1920
    image_height: int = 1080
    max_review_iterations: int = 2
    max_fix_iterations: int = 3
    default_level: str = "/Game/Maps/Default"

    @property
    def ue_api_url(self) -> str:
        return f"http://{self.ue_host}:{self.ue_port}"


def get_claude_auth_env() -> dict:
    tok = os.getenv("ANTHROPIC_AUTH_TOKEN")
    key = os.getenv("ANTHROPIC_API_KEY")
    if tok:
        return {"ANTHROPIC_AUTH_TOKEN": tok}
    if key:
        return {"ANTHROPIC_API_KEY": key}
    creds = Path.home() / ".claude" / ".credentials.json"
    if creds.exists():
        log.info("Claude Code credentials found — CLI will handle auth")
    else:
        log.warning("No auth configured. Set ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY or run 'claude login'")
    return {}
