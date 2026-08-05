"""Environment configuration (secrets live in .env, never in code or DB dumps)."""

import pathlib
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = pathlib.Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BDSK_AI_", env_file=str(_ENV_FILE),
                                      extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8010
    public_base_url: str = "https://support.behdashtik.ir/ai-agent"
    db_path: str = "data/agent.db"
    log_level: str = "INFO"

    admin_token: str = ""
    fernet_key: str = ""

    chatwoot_base_url: str = "http://127.0.0.1:4000"
    chatwoot_public_url: str = "https://support.behdashtik.ir"
    chatwoot_account_id: int = 2
    chatwoot_inbox_id: int = 1
    chatwoot_bot_token: str = ""
    chatwoot_user_token: str = ""
    chatwoot_webhook_secret: str = ""

    hub_base_url: str = "https://hub.behdashtik.ir"
    hub_agent_api_key: str = ""
    hub_webhook_secret: str = ""

    # The production site has its own Chatwoot inbox and its own data hub.
    # Conversations from any other inbox use the default (dev) hub above.
    main_inbox_id: int = 0
    main_hub_base_url: str = "https://mainhub.behdashtik.ir"
    main_hub_agent_api_key: str = ""

    def hub_for_inbox(self, inbox_id: int | None) -> tuple[str, str, str]:
        """(namespace, base_url, api_key) for the store behind this inbox."""
        if self.main_inbox_id and inbox_id == self.main_inbox_id:
            return "main", self.main_hub_base_url, self.main_hub_agent_api_key
        return "dev", self.hub_base_url, self.hub_agent_api_key

    # پل باسلام — تنها درگاه دسترسی به قیمت، موجودی و ارسال کارت محصول
    bridge_base_url: str = "http://127.0.0.1:8103"
    bridge_secret: str = ""
    basalam_vendor_id: int = 0

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    langsmith_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
