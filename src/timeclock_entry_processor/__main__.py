# Standard library imports
from multiprocessing import Queue
from multiprocessing.managers import BaseManager
from os import environ
from pathlib import Path
from sys import platform
from typing import TYPE_CHECKING, Annotated

# Third party imports
import typer
from rich.console import Console

environ["TYPER_USE_RICH "] = "0"

RICH_CONSOLE = Console(
  width=None if platform == "win32" else 165,
  log_time=platform == "win32",
)
PROJECT_NAME = "timeclock_entry_processor"
LOGGING_TYPE = "per_run"

CWD = Path.cwd()


class ClientQueueManager(BaseManager):
  if TYPE_CHECKING:

    def get_shared_queue(self) -> Queue: ...


ClientQueueManager.register("get_shared_queue")

app = typer.Typer()


@app.command()
def cli(
  csv_file: Path,
  manifest_file: Annotated[Path | None, typer.Argument()] = None,
  output_folder: Annotated[Path, typer.Argument()] = CWD / "timeclock_entry_processor_output",
  logging_queue_authkey: Annotated[str | None, typer.Option()] = None,
):
  if not csv_file.exists():
    RICH_CONSOLE.print(f"[red]Error: File '{csv_file}' does not exist.[/red]")
    raise typer.Exit(code=1)
  output_folder.mkdir(parents=True, exist_ok=True)

  if logging_queue_authkey is not None:
    authkey = bytes.fromhex(logging_queue_authkey)
    manager = ClientQueueManager(address=("127.0.0.1", 50000), authkey=authkey)
    manager.connect()
    mp_queue = manager.get_shared_queue()
  else:
    mp_queue = Queue()

    # First party imports
  from aeth_ext import initialize

  initialize(mp_queue, logging="to_queue" if logging_queue_authkey is not None else True)

  # First party imports
  from timeclock_entry_processor import main

  main(mp_queue, csv_file, output_folder, manifest_file)


if __name__ == "__main__":
  app()
