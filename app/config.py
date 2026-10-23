"""  
Config.py :-

This file contains configuration and database connection details
"""

from pathlib import Path

import app
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
class Settings(BaseSettings):
    """
    Settings class for configuration management using Pydantic's BaseSettings.
    """
    app_name    : str = "SkyFiber VOice Agent"
    app_env     : str = "development"
    debug       : bool = True
    database_url: str = "sqlite:///./data/skyfiber.db"
    
    model_config = SettingsConfigDict(
        env_file = PROJECT_ROOT / ".env",
        env_file_encoding = "utf-8",
        extra = "ignore"
    )
settings = Settings()