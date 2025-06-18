from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    SERVICE_NAME: str = "dequeuer"
    RABBITMQ_HOST: str = "rabbitmq"
    RABBITMQ_PORT: int = 5672
    DOWNSTREAM_URL: str = "http://url-fetcher:8000/fetch"
    DEQUEUER_POOL_SIZE: int = 10
    
    # Batch mode settings
    BATCH_MODE: bool = False
    BATCH_DOWNSTREAM_URL: str = "http://url-fetcher:8000/fetch/batch"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings() 