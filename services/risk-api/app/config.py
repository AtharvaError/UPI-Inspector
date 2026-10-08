"""Settings. Values come from the repository's .env file (see .env.example)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]          # repository root
SERVICE_DIR = Path(__file__).resolve().parents[1]   # services/risk-api
APP_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    app_env: str = "local"
    demo_mode: bool = True
    # Who may use the passwordless demo sign-in (only ever in demo mode):
    # off | local (this machine) | lan (this machine and private addresses) | any
    demo_login: str = "local"

    # "pgserver" starts the embedded PostgreSQL under PG_DATA_DIR; otherwise a postgresql:// URL
    database_url: str = "pgserver"
    pg_data_dir: str = ".data/pg"
    database_name: str = "upi"
    # "fakeredis://" keeps the cache inside the API process; otherwise a redis:// URL
    redis_url: str = "fakeredis://"
    event_bus: str = "memory"            # memory | redis | kafka
    kafka_brokers: str = "localhost:9092"
    workers_in_process: bool = True

    jwt_secret: str = "change-me"
    user_token_minutes: int = 30         # an app session ends after this long without use
    staff_token_minutes: int = 60        # a dashboard session likewise
    session_max_hours: int = 12          # and, used or not, this long after sign-in
    clearance_ttl_s: int = 600           # a decision clears a payment for this long after its last approval
    trust_client_biometric: bool = False # accept a bare "biometric passed" from the app, with no device proof
    rate_limit_per_ip: int = 1200        # requests a minute from one address; 0 turns the limit off
    max_body_bytes: int = 65536
    allowed_hosts: str = ""              # extra Host names to answer to, comma-separated; "*" turns the check off
    require_https: bool = False          # refuse plain HTTP from anywhere but this machine
    service_api_keys: str = ""           # "simulator:<key>,mockpsp:<key>"
    phone_hash_key: str = "change-me"
    consortium_key: str = "change-me"
    seed_staff_password: str = "change-me"
    seed_user_pin: str = "000000"

    model_dir: str = "ml/artifacts"
    text_model: str = "tfidf"
    hot_path_timeout_ms: int = 300
    max_inflight: int = 48              # assessments allowed in flight; beyond this a request is shed
    pg_fallback_timeout_ms: int = 40
    graph_job_interval_s: int = 120
    monitor_interval_s: int = 900

    hold_seconds_demo: int = 60
    guardian_timeout_s_demo: int = 120
    cooloff_seconds_demo: int = 10
    unblock_seconds_demo: int = 60       # the 24-hour wait before a personal block lifts, shortened for a demo

    llm_providers: str = "gemini,groq,ollama,template"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.7-flash"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    ollama_url: str = ""
    ollama_model: str = "llama3.2"

    # Which web pages may call the API from a browser: "local" (pages served from this machine or a private
    # address), a comma-separated list of origins, or "*" for any page
    cors_origins: str = "local"
    public_base_url: str = "http://localhost:8000"

    @property
    def api_keys(self) -> dict[str, str]:
        """key -> service name"""
        out: dict[str, str] = {}
        for part in self.service_api_keys.split(","):
            if ":" in part:
                name, key = part.split(":", 1)
                out[key.strip()] = name.strip()
        return out

    @property
    def model_path(self) -> Path:
        p = Path(self.model_dir)
        return p if p.is_absolute() else ROOT / p


@lru_cache
def get_settings() -> Settings:
    return Settings()
