from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    startup_delay_seconds: float = 0.0
    request_processing_seconds: float = 0.0


settings = Settings()