from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    app_name: str = 'CRRO'
    environment: str = 'development'
    database_url: str = 'sqlite:///./crro.db'
    razorpay_key_id: str = ''
    razorpay_key_secret: str = ''
    razorpay_webhook_secret: str = ''
    openai_api_key: str = ''
    openai_model: str = 'gpt-5-mini'
    public_base_url: str = 'http://localhost:8000'
    approval_amount_inr: float = 50000
    max_attempts: int = 3

@lru_cache
def get_settings(): return Settings()
