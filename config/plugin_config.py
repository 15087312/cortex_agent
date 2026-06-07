from pathlib import Path
from pydantic import BaseModel, Field


class PluginConfig(BaseModel):
    plugins_dir: str = "data/plugins"
    engine_enabled: bool = True
    require_signatures: bool = False
    require_enforced_sandbox: bool = False
    production_mode: bool = False
    sandbox_backend: str = "auto"
