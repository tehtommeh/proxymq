from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # RabbitMQ Configuration
    RABBITMQ_HOST: str = "rabbitmq"
    RABBITMQ_PORT: int = 5672
    ENQUEUER_REPLY_TIMEOUT: int = 120
    ENQUEUER_POOL_SIZE: int = 10
    
    # Redis Configuration (for async mode)
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0
    
    # Async Mode Configuration
    ASYNC_MODE: bool = False
    ASYNC_JOB_TTL: int = 3600  # Job TTL in seconds (1 hour)
    ASYNC_AUTH_HEADER: str = "Authorization"  # Header name for job authentication
    ASYNC_REQUIRE_AUTH: bool = False  # If True, all async jobs must include auth header

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings() 