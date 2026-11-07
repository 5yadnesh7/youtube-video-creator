from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    openrouter_api_key: str = ""
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    cors_origins: list[str] = ["http://localhost:3000"]

    data_dir: Path = ROOT / "data"
    music_dir: Path = ROOT / "music"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"


settings = Settings()
