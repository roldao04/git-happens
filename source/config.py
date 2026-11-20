"""Settings from environment variables (and a local .env, if present)."""
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    poll_seconds: float
    heartbeat_minutes: float
    stale_minutes: float
    data_dir: Path
    log_level: str
    openrouter_api_key: str | None
    agent_model: str
    agent_max_steps: int
    camera_rtsp_url: str | None = field(default=None, repr=False)


def load_settings() -> Settings:
    _load_dotenv(REPO_ROOT / ".env")
    data_dir = Path(os.environ.get("LOOP_DATA_DIR", REPO_ROOT / "data"))
    return Settings(
        api_key=os.environ.get("CGH_API_KEY"),
        poll_seconds=float(os.environ.get("LOOP_POLL_SECONDS", 20)),
        heartbeat_minutes=float(os.environ.get("LOOP_HEARTBEAT_MINUTES", 15)),
        stale_minutes=float(os.environ.get("LOOP_STALE_MINUTES", 60)),
        data_dir=data_dir,
        log_level=os.environ.get("LOOP_LOG_LEVEL", "INFO"),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY") or None,
        agent_model=os.environ.get("AGENT_MODEL", "anthropic/claude-sonnet-4.5"),
        agent_max_steps=int(os.environ.get("AGENT_MAX_STEPS", 12)),
        camera_rtsp_url=os.environ.get("CAMERA_RTSP_URL") or None,
    )
