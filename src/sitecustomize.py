from os import environ

environ.setdefault("PYDANTIC_ERRORS_INCLUDE_URL", "false")


from logging_config import configure_logging  # noqa: E402

configure_logging()
