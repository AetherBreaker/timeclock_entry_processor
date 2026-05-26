if __name__ == "__main__":
  from os import environ
  from sys import platform

  from rich.console import Console
  from sft_ext.logging_ext.init_logging import init_logging

  environ["TYPER_USE_RICH "] = "0"

  RICH_CONSOLE = Console(
    width=None if platform == "win32" else 165,
    log_time=platform == "win32",
  )
  PROJECT_NAME = "timeclock_entry_processor"
  LOGGING_TYPE = "daily"

  from multiprocessing import Queue

  mp_queue = Queue()

  init_logging(mp_queue)


from pathlib import Path

import typer
from start import main


def cli(csv_file: Path):
  if not csv_file.exists():
    RICH_CONSOLE.print(f"[red]Error: File '{csv_file}' does not exist.[/red]")
    raise typer.Exit(code=1)

  main(mp_queue, csv_file)


if __name__ == "__main__":
  typer.run(cli)
