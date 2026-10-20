"""Install configuration.

Every knob comes from the environment (or the project `.env`). Nothing here is a
secret by itself; the secret *values* (API keys, mail passwords, the encryption
key) are read from the environment and never written back anywhere.

Only what an install needs before its admin is reachable lives here (database,
secrets, models, mail, addresses). Every business number — budgets of agents,
schedules, limits, texts — is a setting in the admin, never here.
"""

import os
from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ExecutionMode = Literal["development", "test", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- runtime -----------------------------------------------------------
    execution_mode: ExecutionMode = "development"
    # The name of the company this install runs for: shown in the admin, mail and the agents' instructions.
    app_name: str = "Control Tower"
    # The owner's time zone (IANA name, e.g. Europe/Berlin): schedules, posting hours and the days of Insights.
    time_zone: str = "UTC"
    public_base_url: str = ""  # e.g. https://tower.example.com — the gateway's and the media links' origin
    log_level: str = "INFO"
    log_json: bool = True

    # --- database ------------------------------------------------------------
    database_engine: Literal["sqlite", "postgresql", "mysql"] = "sqlite"
    database_url: str = ""
    database_read_only: bool = False
    database_ownership: Literal["external", "application"] = "application"
    database_ssl_root_cert: str = ""
    database_ssl_mode: Literal["verify-full", "verify-ca", "require", "prefer", "disable"] = (
        "verify-full"
    )
    data_dir: str = "../.runtime-data"
    # Python plugins: one folder each with a plugin.py (docs/PLUGINS.md). Missing folder = none.
    plugins_dir: str = "../plugins"
    # Migrations may run as another role than the server's (empty: DATABASE_URL).
    database_migration_url: str = ""

    # --- redis / workers -------------------------------------------------------
    redis_url: str = ""  # optional; when empty the in-process event bus is used
    embedded_worker: bool = True  # run the worker loop inside the server process (development)
    embedded_scheduler: bool = True
    worker_poll_interval_seconds: float = 2.0
    worker_concurrency: int = 4
    max_concurrent_ai_tasks: int = 2  # SQLite serialises writers anyway; PostgreSQL can raise this

    # --- sign-in -----------------------------------------------------------------
    login_max_attempts: int = 8
    login_lockout_minutes: int = 15
    # Who sends the e-mails, in every footer (empty: the app name).
    operator_name: str = ""
    # A person and a postal address in e-mail footers, only when set (empty: none shown).
    operator_owner: str = ""
    operator_address: str = ""
    # The company's own website, contact address and legal pages, for e-mail footers and the QA checks (empty: none).
    public_site_url: str = ""
    support_email: str = ""
    imprint_url: str = ""
    privacy_url: str = ""
    cors_allowed_origins: str = ""  # comma separated; empty = same-origin only

    # --- virus scanner for attachments (services/scanner.py) ---------------------
    clamd_host: str = ""
    clamd_port: int = 3310

    # --- the studio: pictures and videos -------------------------------------------
    # The picture models and their list prices (logged against the owner's monthly picture budget). Change with the
    # vendor's prices.
    gemini_image_model: str = "gemini-3.1-flash-image"
    gemini_image_model_best: str = "gemini-3-pro-image"
    ai_image_price_usd: float = 0.07
    ai_image_price_best_usd: float = 0.14
    # The video models and their list prices per second of video (720p, with sound), logged against the monthly video budget.
    gemini_video_model: str = "veo-3.1-fast-generate-preview"
    gemini_video_model_best: str = "veo-3.1-generate-preview"
    ai_video_price_per_second_usd: float = 0.10
    ai_video_price_per_second_best_usd: float = 0.40
    # Where finished videos are kept until the owner deletes them.
    marketing_dir: str = "../.runtime-data/marketing"
    # The social networks' API versions (each network retires old versions; update when it says so).
    meta_graph_version: str = "v23.0"
    linkedin_api_version: str = "202509"
    # Mail: mail servers on private addresses are refused (an account's server is entered in the admin app); tests and
    # a local development server switch this on.
    webmail_allow_private_hosts: bool = False
    # What needs the owner is mailed here ("" = only the admin bell).
    admin_alert_email: str = ""

    # --- Insights ----------------------------------------------------------------------
    # Page speed and error reports from the admin app's browsers (anonymous). Off: ignored.
    insights_telemetry_enabled: bool = True
    # Public addresses the worker checks every minute: "name=url,name=url". Empty: the admin and the gateway.
    insights_uptime_targets: str = ""

    # --- the admin -----------------------------------------------------------------------
    admin_public_base_url: str = ""
    admin_session_cookie_name: str = "tower_admin"
    admin_session_hours: int = 8
    admin_idle_minutes: int = 30
    admin_ip_allowlist: str = ""  # comma separated; empty = any address
    admin_frontend_dist: str = ""  # built admin app; empty → Vite serves it in development
    admin_totp_issuer: str = ""  # empty: "<app name> Admin"

    # --- secrets ----------------------------------------------------------------
    encryption_secret: str = ""  # AEAD key material for stored secrets (admin TOTP, mailbox passwords, provider keys)

    # --- AI (Gemini) -------------------------------------------------------------
    gemini_api_key: str = ""
    # Nia's browser: the internal browser service and the model that looks at its screen.
    nia_browser_url: str = "http://browser:8100"
    nia_browser_model: str = "gemini-3.8-flash"
    gemini_model: str = "gemini-3.8-flash"  # default for the reasoning tier
    gemini_reasoning_model: str = ""
    gemini_fast_model: str = "gemini-3.5-flash-lite"
    gemini_embedding_model: str = "gemini-embedding-2"
    gemini_live_model: str = "gemini-3.8-live"
    assistant_session_minutes: int = 20
    assistant_daily_voice_minutes: int = 60
    assistant_idle_seconds: int = 180
    gemini_timeout_seconds: float = 60.0
    gemini_max_retries: int = 3
    # The install's last safety rail across all AI use (each agent also has its own budget in the admin).
    ai_daily_budget_usd: float = 5.0
    ai_monthly_budget_usd: float = 60.0
    # The text price for a model not in the list below (USD per million tokens; thinking counts as output).
    ai_input_price_per_million_usd: float = 0.30
    ai_output_price_per_million_usd: float = 2.50
    # List prices per model (USD per million tokens: [input, output incl. thinking]); a model is matched by its name or
    # the start of it. Change with the vendor's prices (AI_MODEL_PRICES as JSON).
    ai_model_prices: dict[str, list[float]] = {"gemini-3.5-flash-lite": [0.30, 2.50], "gemini-3.8-flash": [0.75, 3.75]}
    # Voice (Gemini Live): per minute of audio heard and spoken. AI_VOICE_PRICE_PER_MINUTE_USD, when set, prices every
    # minute alike instead.
    ai_voice_input_price_per_minute_usd: float = 0.005
    ai_voice_output_price_per_minute_usd: float = 0.018
    ai_voice_price_per_minute_usd: float = 0.0

    # --- the install's own mailbox (system mail) -----------------------------------
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    # The mailbox's IMAP server, where returned e-mails arrive (services/mail_bounces.py).
    # Empty: the SMTP host with "smtp." replaced by "imap.".
    mail_imap_host: str = ""
    mail_imap_port: int = 993
    smtp_use_tls: bool = True

    @model_validator(mode="after")
    def _derive(self) -> "Settings":
        if not self.gemini_reasoning_model:
            self.gemini_reasoning_model = self.gemini_model
        if not self.operator_name:
            self.operator_name = self.app_name
        if not self.admin_totp_issuer:
            self.admin_totp_issuer = f"{self.app_name} Admin"
        if self.execution_mode == "production" and len(self.encryption_secret) < 32:
            raise ValueError("ENCRYPTION_SECRET must be at least 32 characters in production")
        return self

    # --- derived helpers -----------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.execution_mode == "production"

    @property
    def cookie_secure(self) -> bool:
        return self.is_production

    @property
    def smtp_login(self) -> str:
        return self.smtp_username or self.smtp_user

    def csv(self, value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def settings() -> Settings:
    # The test suite never reads a server's .env (production secrets, the admin's IP list): only its own environment.
    if os.environ.get("EXECUTION_MODE") == "test":
        return Settings(_env_file=None)
    return Settings()


__all__ = ["ExecutionMode", "Settings", "settings"]
