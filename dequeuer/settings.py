from pydantic import BaseSettings

class Settings(BaseSettings):
    SERVICE_NAME: str
    DOWNSTREAM_URL: str
    RABBITMQ_HOST: str = "rabbitmq"
    RABBITMQ_PORT: int = 5672
    DEQUEUER_POOL_SIZE: int = 10

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings() 