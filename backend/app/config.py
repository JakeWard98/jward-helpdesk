"""Application configuration.

Every setting comes from the environment. In ``prod`` the settings object
refuses to build on a missing or obviously-placeholder secret, so a
misconfigured stack fails at boot rather than serving with a known key.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_MARKERS = ("change_me", "changeme", "example.com", "replace_me")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- core ----
    app_env: Literal["dev", "prod"] = "prod"
    app_secret: str = Field(min_length=32)
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8080"
    cors_origins: str = ""
    helpdesk_role: Literal["api", "worker"] = "api"

    # ---- database ----
    database_url: str = "postgresql+psycopg://helpdesk:helpdesk@db:5432/helpdesk"

    # ---- bootstrap ----
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    require_mfa_for_agents: bool = True

    # ---- sessions ----
    session_idle_timeout_minutes: int = 120
    session_absolute_timeout_hours: int = 12
    session_cookie_secure: bool = True

    # ---- outbound mail ----
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    # Path to a PEM certificate to trust, for a local relay such as Proton
    # Mail Bridge. Verification stays on, against this certificate.
    smtp_ca_cert: str = ""
    # Skips verification altogether. Only honoured for a local host - see
    # services/mail_tls.py.
    smtp_tls_insecure: bool = False
    smtp_from_name: str = "Helpdesk"
    smtp_from_email: str = ""
    smtp_reply_to_email: str = ""

    # ---- inbound mail ----
    inbound_email_enabled: bool = False
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    # ssl = implicit TLS (port 993), starttls = upgrade after connecting
    # (Proton Mail Bridge uses this on 1143), none = plaintext, local only.
    imap_security: Literal["ssl", "starttls", "none"] = "ssl"
    imap_ca_cert: str = ""
    imap_tls_insecure: bool = False
    imap_folder: str = "INBOX"
    imap_processed_folder: str = ""
    imap_poll_seconds: int = 60

    # ---- attachments ----
    attachment_dir: str = "/data/attachments"
    max_attachment_mb: int = 25
    max_email_mb: int = 40

    # ---- cloudflare ----
    trust_cloudflare_headers: bool = True
    cf_access_enabled: bool = False
    cf_access_team_domain: str = ""
    cf_access_aud: str = ""

    # ---- brute-force protection ----
    login_max_attempts: int = 8
    login_window_seconds: int = 900
    login_lockout_seconds: int = 900

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def allowed_origins(self) -> list[str]:
        raw = self.cors_origins or self.public_base_url
        return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]

    @property
    def max_attachment_bytes(self) -> int:
        return self.max_attachment_mb * 1024 * 1024

    @property
    def max_email_bytes(self) -> int:
        return self.max_email_mb * 1024 * 1024

    @property
    def outbound_email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from_email)

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        if not self.is_prod:
            return self

        problems: list[str] = []
        lowered = self.app_secret.lower()
        if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
            problems.append("APP_SECRET still contains a placeholder value")
        if len(set(self.app_secret)) < 8:
            problems.append("APP_SECRET is not random enough")
        if not self.public_base_url.startswith("https://"):
            problems.append("PUBLIC_BASE_URL must be https:// in production")
        if not self.session_cookie_secure:
            problems.append("SESSION_COOKIE_SECURE must stay true in production")
        if self.bootstrap_admin_password and any(
            m in self.bootstrap_admin_password.lower() for m in PLACEHOLDER_MARKERS
        ):
            problems.append("BOOTSTRAP_ADMIN_PASSWORD still contains a placeholder value")
        if self.inbound_email_enabled and not self.imap_host:
            problems.append("INBOUND_EMAIL_ENABLED is true but IMAP_HOST is empty")

        # Catch an unverified connection to a remote mail server at boot
        # rather than at the first poll.
        from app.services.mail_tls import is_local_host

        if self.smtp_tls_insecure and not is_local_host(self.smtp_host):
            problems.append(
                f"SMTP_TLS_INSECURE is set but SMTP_HOST ({self.smtp_host}) is not local"
            )
        if self.imap_tls_insecure and not is_local_host(self.imap_host):
            problems.append(
                f"IMAP_TLS_INSECURE is set but IMAP_HOST ({self.imap_host}) is not local"
            )
        if self.imap_security == "none" and not is_local_host(self.imap_host):
            problems.append(
                f"IMAP_SECURITY=none is only allowed for a local host, not {self.imap_host}"
            )

        if problems:
            raise ValueError(
                "Refusing to start in production:\n  - " + "\n  - ".join(problems)
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except Exception as exc:  # pragma: no cover - startup path
        print(f"[config] invalid configuration: {exc}", file=sys.stderr)
        raise


settings = get_settings()
