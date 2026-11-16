from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 저장소 루트의 .env 하나만 읽는다(어느 폴더에서 실행하든 같은 값).
# 컨테이너에는 이 파일이 없고 환경 변수로 받는다.
ENV_FILE = Path(__file__).resolve().parents[4] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    database_url: str = "postgresql+psycopg://whyuseguide:whyuseguide@localhost:15432/whyuseguide"
    redis_url: str = "redis://localhost:16379/0"
    otp_url: str = "http://localhost:18080"
    otp_timed_legs: bool = True  # 일정 구간을 OTP 시간표로 계산(끄면 엔진 근사만)
    otp_timeout_s: float = 10.0
    otp_concurrency: int = 8  # 동시에 보내는 OTP 질의 수(앱 전체)
    # 구간 결과를 Redis에 두는 시간(초). 0이면 캐시하지 않는다. 시간표 갱신(주 1회)보다 충분히 짧게.
    otp_cache_ttl_s: int = 6 * 60 * 60
    health_timeout_s: float = 3.0
    log_level: str = "INFO"
