from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://whyuseguide:whyuseguide@localhost:15432/whyuseguide"
    redis_url: str = "redis://localhost:16379/0"
    otp_url: str = "http://localhost:18080"
    health_timeout_s: float = 3.0
