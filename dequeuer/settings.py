from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    SERVICE_NAME: str = "dequeuer"
    RABBITMQ_HOST: str = "rabbitmq"
    RABBITMQ_PORT: int = 5672
    DOWNSTREAM_URL: str = "http://url-fetcher:8000/fetch"
    DEQUEUER_POOL_SIZE: int = 10
    DOWNSTREAM_TIMEOUT: int = 60  # HTTP timeout for downstream requests
    
    # Batch mode settings
    BATCH_MODE: bool = False
    BATCH_DOWNSTREAM_URL: str = "http://url-fetcher:8000/fetch/batch"
    
    # Health check settings
    HEALTH_CHECK_URL: Optional[str] = None  # If not provided, skip health check
    HEALTH_CHECK_INTERVAL: int = 5  # Seconds between health check attempts
    HEALTH_CHECK_TIMEOUT: int = 10  # HTTP timeout for health check requests
    HEALTH_CHECK_MAX_RETRIES: int = 0  # Maximum retries before failing (0 = infinite)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings() 