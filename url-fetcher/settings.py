from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    BATCH_SIZE: int = 10

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings() 