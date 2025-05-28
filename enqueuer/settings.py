from pydantic import BaseSettings

class Settings(BaseSettings):
    RABBITMQ_HOST: str = "rabbitmq"
    RABBITMQ_PORT: int = 5672
    ENQUEUER_REPLY_TIMEOUT: int = 30
    ENQUEUER_POOL_SIZE: int = 10

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings() 