if __name__ == "__main__":
  # Standard library imports
  from os import environ
  from sys import platform

  # Third party imports
  from rich.console import Console

  # First party imports
  from sft_ext.logging.init_logging import init_logging

  environ["TYPER_USE_RICH "] = "0"

  RICH_CONSOLE = Console(
    width=None if platform == "win32" else 165,
    log_time=platform == "win32",
  )
  PROJECT_NAME = "timeclock_entry_processor"
  LOGGING_TYPE = "daily"

  # Standard library imports
  from multiprocessing import Queue

  mp_queue = Queue()

  init_logging(mp_queue)


# Standard library imports
from pathlib import Path  # noqa: TC003
from typing import Annotated

# Third party imports
import typer

# First party imports
from timeclock_entry_processor import main
from timeclock_entry_processor.environment_init_vars import CWD


def cli(
  csv_file: Path,
  manifest_file: Annotated[Path | None, typer.Argument()] = None,
  output_folder: Annotated[Path, typer.Argument()] = CWD / "timeclock_entry_processor_output",
):
  if not csv_file.exists():
    RICH_CONSOLE.print(f"[red]Error: File '{csv_file}' does not exist.[/red]")
    raise typer.Exit(code=1)
  output_folder.mkdir(parents=True, exist_ok=True)

  main(mp_queue, csv_file, output_folder, manifest_file)


if __name__ == "__main__":
  typer.run(cli)
  # cli(Path.cwd() / "input" / "Time-Clock-Entry-Report_2026-05-08_17-53-58.csv", manifest_file=Path.cwd() / "manifest.json")
