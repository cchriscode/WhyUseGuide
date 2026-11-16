from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://whyuseguide:whyuseguide@localhost:15432/whyuseguide"
    redis_url: str = "redis://localhost:16379/0"
    otp_url: str = "http://localhost:18080"
    otp_timed_legs: bool = True  # 일정 구간을 OTP 시간표로 계산(끄면 엔진 근사만)
    otp_timeout_s: float = 10.0
    health_timeout_s: float = 3.0
