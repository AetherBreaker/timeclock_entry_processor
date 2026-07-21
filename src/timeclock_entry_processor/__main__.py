if True:  # Prevent Ruff E402 warnings
  # Standard library imports
  from os import environ
  from sys import platform

  # Third party imports
  from rich.console import Console

  # First party imports
  from aeth_ext import initialize

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

  initialize(mp_queue, logging="to_queue")


# Standard library imports
from multiprocessing.managers import BaseManager
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated, override

# Third party imports
import typer

# First party imports
from timeclock_entry_processor import main
from timeclock_entry_processor.environment_init_vars import CWD

if TYPE_CHECKING:
  # Standard library imports
  from types import TracebackType


class ClientQueueManager(BaseManager):
  @override
  def __enter__(self):
    self.connect()
    return self

  @override
  def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None):
    pass


app = typer.Typer()


@app.command()
def cli(
  csv_file: Path,
  manifest_file: Annotated[Path | None, typer.Argument()] = None,
  output_folder: Annotated[Path, typer.Argument()] = CWD / "timeclock_entry_processor_output",
  logging_queue_authkey: Annotated[bytes | None, typer.Option()] = None,
):
  if not csv_file.exists():
    RICH_CONSOLE.print(f"[red]Error: File '{csv_file}' does not exist.[/red]")
    raise typer.Exit(code=1)
  output_folder.mkdir(parents=True, exist_ok=True)

  if logging_queue_authkey is not None:
    ClientQueueManager.register("get_shared_queue")
    manager = ClientQueueManager(address=("127.0.0.1", 50000), authkey=logging_queue_authkey)
    manager.connect()

  main(mp_queue, csv_file, output_folder, manifest_file)


if __name__ == "__main__":
  app()
