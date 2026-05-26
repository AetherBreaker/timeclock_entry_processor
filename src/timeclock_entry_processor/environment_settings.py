import sys
from logging import getLogger
from os import environ
from pathlib import Path

from pydantic_settings import SettingsConfigDict
from sft_ext.settings import BaseSettings

logger = getLogger(__name__)

environ.setdefault("PYDANTIC_ERRORS_INCLUDE_URL", "false")

_CWD = Path(__file__).parent if getattr(sys, "frozen", False) else Path.cwd()


class Settings(BaseSettings):
  model_config = (
    SettingsConfigDict(
      env_file=_CWD / "testing.env",
      env_file_encoding="utf-8",
      env_ignore_empty=True,
    )
    if __debug__
    else SettingsConfigDict()
  )
