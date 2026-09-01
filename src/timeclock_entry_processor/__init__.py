"""
Employee Time Clock Entry PDF Generator

Reads time clock data from CSV and generates a PDF timeline visualization
showing when each employee was clocked in. Each employee is color-coded,
and data is organized by calendar week (Monday-Sunday).
"""

if __name__ == "__main__":
  # Standard library imports
  from multiprocessing import Queue

  # First party imports
  from aeth_ext import initialize

  mp_queue = Queue()

  initialize(mp_queue)
else:
  # Third party imports
  from rich import get_console

  RICH_CONSOLE = get_console()

# Standard library imports
import pickle
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import date, time, timedelta
from json import dump
from logging import getLogger
from multiprocessing import Queue
from pathlib import Path
from sys import stderr
from traceback import format_exception
from typing import TYPE_CHECKING, NoReturn, TypedDict

# Third party imports
from pandas import DataFrame, concat, read_csv, to_datetime, to_numeric

# First party imports
from aeth_ext.rich.progress import Progress
from timeclock_entry_processor.employee_info import get_employee_info
from timeclock_entry_processor.environment_init_vars import CWD
from timeclock_entry_processor.pdf_gen import TimelinePDF, init_pdf_worker, start_mp_pdf_gen

if TYPE_CHECKING:
  # First party imports
  from aeth_ext.errors.exception_trail import ExceptionTrail
  from aeth_ext.logging.bases import TaggedLogRecord

logger = getLogger(__name__)

SHUTDOWN_EXIT_CODE = 143
"""Fixed exit code for the self-termination shutdown policy (128 + SIGTERM by convention), so a
consumer can distinguish "told to stop" from a crash. The manifest is never written on this path,
so ScheduledReportAggregator fails the job either way."""

# The live PDF worker pool, exposed to the shutdown callback below. Set for exactly the lifetime
# of main()'s executor block; a shutdown signal landing outside that window is a no-op.
_active_procpool: ProcessPoolExecutor | None = None


def _kill_worker_pool(trails: tuple[ExceptionTrail, ...]) -> None:
  """Shutdown-registry callback: immediately terminate the PDF worker pool.

  Registered at default priority so it runs *before* aeth_ext's logging-transport teardown
  (``LOGGING_TRANSPORT_PRIORITY``): killing the workers first stops new log records from being
  generated while the in-flight ones flush, and stops burning CPU on results that a cancelled run
  makes useless -- everything is regenerated next run.
  """
  pool = _active_procpool
  if pool is None:
    return
  pool.shutdown(wait=False, cancel_futures=True)
  # shutdown() never stops a worker mid-task; kill the worker processes directly. `_processes` is
  # private but stable -- accessed defensively so an executor-internals change degrades to
  # "workers finish their current task" rather than an error inside the shutdown pass.
  for proc in tuple((getattr(pool, "_processes", None) or {}).values()):
    try:
      proc.kill()
    except OSError:
      pass


def _terminate_process(trails: tuple[ExceptionTrail, ...]) -> NoReturn:
  """Shutdown-registry callback: hard-exit once the log flush is done.

  Registered *after* ``LOGGING_TRANSPORT_PRIORITY`` so it runs once aeth_ext has flushed the
  logging transport (the mp-queue feeder), then acts as if the process had received SIGKILL --
  this program's own shutdown policy: flush in-flight log records, terminate, and never hold a
  consumer's shutdown hostage on remaining teardown.
  """
  # Standard library imports
  from os import _exit

  _exit(SHUTDOWN_EXIT_CODE)


def _register_shutdown_policy() -> None:
  """Register this program's shutdown policy with aeth_ext's shutdown registry.

  Gated on ``python -O`` exactly like aeth_ext's own signal handling -- under a plain dev
  interpreter no handlers are installed and Ctrl+C keeps stock behaviour, so this would never run.
  """
  if __debug__:
    return
  # First party imports
  from aeth_ext.errors.shutdown import LOGGING_TRANSPORT_PRIORITY, ShutdownPhase, register_for_shutdown

  register_for_shutdown(_kill_worker_pool, phase=ShutdownPhase.THREADED, required=True)
  register_for_shutdown(_terminate_process, phase=ShutdownPhase.THREADED, required=True, priority=LOGGING_TRANSPORT_PRIORITY + 1000)


type ProcessResult = list[tuple[list[str], dict[str, str], int, date, date, DataFrame, time, time, Path]]


DEFAULT_OUT_TIME = time(21, 0)  # 9:00 PM
HOURS_PER_DAY = 24

EXPECTED_HEADERS = [
  "Store",
  "Employee Name",
  "In Time",
  "Out Time",
  "Biometric In",
  "Biometric Out",
  "Change History",
  "Time Worked",
  "Manual",
  "Edited",
  "Entry Type",
]


def load_and_parse_data(csv_path: Path) -> DataFrame:
  """Load CSV and parse datetime columns."""
  df = read_csv(
    csv_path,
    header=0,
    names=EXPECTED_HEADERS,
    dtype=str,  # Load all as string to handle parsing manually and avoid dtype issues with
    usecols=[
      "Store",
      "Employee Name",
      "In Time",
      "Out Time",
      "Time Worked",
    ],
  )

  # Filter out summary rows (Grand Totals, empty rows)
  df = df[df["Employee Name"].notna() & (df["Employee Name"] != "")]

  # Parse datetime columns
  df["In Time"] = to_datetime(df["In Time"], format="%m/%d/%Y %I:%M %p")

  # Handle missing Out Time - set to 9 PM on the same day (vectorized)
  df["Out Time"] = df["Out Time"].replace("N/A", None)
  df["Out Time"] = to_datetime(df["Out Time"], format="%m/%d/%Y %I:%M %p", errors="coerce")

  # For missing Out Time, set to 9 PM on the In Time date (vectorized)
  mask = df["Out Time"].isna()
  df.loc[mask, "Out Time"] = df.loc[mask, "In Time"].dt.floor("D") + timedelta(hours=DEFAULT_OUT_TIME.hour)

  # Parse "Time Worked" column to float (fully vectorized via to_numeric)
  df["Hours Worked"] = to_numeric(
    df["Time Worked"].fillna("0 Hours").astype(str).str.replace(" Hours", "").str.strip(),
    errors="coerce",
  ).fillna(0.0)

  # Extract date for grouping (already vectorized)
  df["Date"] = df["In Time"].dt.date

  # Extract store number from "Store" column (vectorized)
  # Format: "13 - Sweet Fire Tobacco 013" -> "013"
  df["Store Number"] = df["Store"].astype(str).str.split(" - ").str[0].str.strip().str.zfill(3)
  df["Store Number"] = df["Store Number"].where(df["Store"].notna() & df["Store"].astype(str).str.contains(" - "), "Unknown")

  return df


def group_by_weeks(df: DataFrame) -> dict[tuple[date, date], DataFrame]:
  """Group data by calendar weeks (Monday-Sunday)."""
  weeks = {}

  for dt in df["Date"].unique():
    dt: date
    # Get the Monday of the week containing this date
    # weekday(): Monday=0, Sunday=6
    days_since_monday = dt.weekday()
    week_start = dt - timedelta(days=days_since_monday)
    week_end = week_start + timedelta(days=6)

    week_key = (week_start, week_end)

    if week_key not in weeks:
      weeks[week_key] = []

    weeks[week_key].append(dt)

  # Sort weeks and create dataframes
  sorted_weeks = {}
  for week_key in sorted(weeks.keys()):
    dates = weeks[week_key]
    week_df = df[df["Date"].isin(dates)]
    sorted_weeks[week_key] = week_df

  return sorted_weeks


def calculate_time_range(df: DataFrame) -> tuple[time, time]:
  """Calculate the min and max times across all data for auto-fitted axis."""
  all_times = concat([df["In Time"].dt.time, df["Out Time"].dt.time])

  min_time = all_times.min()
  max_time = all_times.max()

  # Round down to nearest hour for min, up for max
  min_hour = min_time.hour
  max_hour = max_time.hour if max_time.minute == 0 else max_time.hour + 1

  return time(min_hour, 0), time(min(max_hour, 23), 0 if max_hour < HOURS_PER_DAY else 59)


class ManifestEntry(TypedDict):
  csv: Path
  pdf: Path


def process_store_data(
  store_number: int,
  store_df: DataFrame,
  employee_id_to_group: dict[str, str],
  output_base: Path,
  manifest: dict[int, dict[str, ManifestEntry]] | None = None,
) -> ProcessResult:

  unique_employees: list[str] = store_df["Employee Name"].unique().tolist()

  weeks = group_by_weeks(store_df)

  min_time, max_time = calculate_time_range(store_df)

  store_output_dir = output_base / f"SFT{store_number:0>3}"
  store_output_dir.mkdir(parents=True, exist_ok=True)

  week_args = []

  for (week_start, week_end), week_df in weeks.items():
    week_folder = store_output_dir / f"Week Ending {week_end.strftime('%Y-%m-%d')}"
    week_folder.mkdir(exist_ok=True)
    pdf_path = week_folder / f"Week Ending {week_end.strftime('%Y-%m-%d')}.pdf"

    csv_path = week_folder / f"Week Ending {week_end.strftime('%Y-%m-%d')}.csv"

    week_df.to_csv(csv_path, index=False)

    if manifest is not None:
      manifest.setdefault(store_number, {})[str(week_end)] = ManifestEntry(csv=csv_path, pdf=pdf_path)

    week_args.append(
      (
        unique_employees,
        employee_id_to_group,
        store_number,
        week_start,
        week_end,
        week_df,
        min_time,
        max_time,
        pdf_path,
      )
    )
  return week_args


def _report_failures_and_exit(failures: list[tuple[int, date, BaseException]], total: int) -> NoReturn:
  """Report failed PDF tasks and exit non-zero, *before* the manifest is written.

  All-or-nothing: exiting here keeps the manifest's written-last commit-marker property, so a
  consumer never sees partial output. The tracebacks go to stderr (in addition to the log records
  already emitted per failure) because that is the subprocess contract with
  ScheduledReportAggregator: it tees stderr into ``CalledProcessError.stderr`` and decides from
  there whether to send a job-failed alert.
  """
  stderr.write(f"{len(failures)} of {total} PDF generation task(s) failed:\n")
  for store_number, week_end, exc in failures:
    stderr.write(f"\n--- store {store_number}, week ending {week_end} ---\n")
    stderr.writelines(format_exception(exc))
  raise SystemExit(1)


def main(mp_queue: Queue[TaggedLogRecord], input_path: Path, output_folder: Path, manifest_file: Path | None) -> None:
  global _active_procpool

  _register_shutdown_policy()

  font_input_folder = CWD / "font_input"
  if not font_input_folder.exists():
    raise FileNotFoundError(f"Font input folder not found at {font_input_folder}. Please create it and add necessary font files.")

  # Load employee group information
  employee_info = get_employee_info()
  # Create a dictionary mapping employee ID to group for O(1) lookups (instead of DataFrame filtering)
  employee_id_to_group: dict[str, str] = dict(zip(employee_info["id"], employee_info["group"], strict=False))

  df = load_and_parse_data(input_path)
  logger.info("Total entries loaded: %d\nOverall date range: %s to %s", len(df), df["Date"].min(), df["Date"].max())

  # Initialize reusable TimelinePDF class pickle
  initial = TimelinePDF(font_input_folder)
  pickled_pdf_inst = pickle.dumps(initial, protocol=pickle.HIGHEST_PROTOCOL)

  # Group by store
  stores = df.groupby("Store Number")

  manifest = None

  if manifest_file is not None:
    manifest = {}

  failures: list[tuple[int, date, BaseException]] = []
  future_to_week: dict[Future[None], tuple[int, date]] = {}

  with (
    Progress(console=RICH_CONSOLE, auto_refresh=False) as progress,
    ProcessPoolExecutor(
      # max_workers=1,
      initializer=init_pdf_worker,
      initargs=(mp_queue, pickled_pdf_inst),
    ) as procpool,
    ThreadPoolExecutor(
      # max_workers=1,
    ) as threadpool,
  ):
    _active_procpool = procpool
    with progress.add_task("[magenta]Processing weeks...") as data_task:
      thread_futures = [
        threadpool.submit(
          process_store_data,
          int(store_number),  # type: ignore
          store_df,
          employee_id_to_group,
          output_folder,
          manifest,
        )
        for store_number, store_df in stores
      ]
      for future in as_completed(thread_futures):
        for week_args in future.result():
          proc_future = procpool.submit(start_mp_pdf_gen, *week_args)
          future_to_week[proc_future] = (week_args[2], week_args[4])  # store number, week-ending date

      # Drain the PDF futures *inside* the progress-task block so the task outlives every
      # completion -- a future finishing after the task was removed is what produced the
      # swallowed `KeyError: 0` storm from the old done-callback. Checking each future here is
      # also what makes worker failures fail the run: a done-callback that raises is logged and
      # DISCARDED by concurrent.futures, which previously let a failed week exit 0 with a
      # manifest entry pointing at a PDF that was never written.
      for proc_future in as_completed(future_to_week):
        store_number, week_end = future_to_week[proc_future]
        exc = proc_future.exception()
        if exc is not None:
          failures.append((store_number, week_end, exc))
          logger.error("PDF generation failed for store %s, week ending %s", store_number, week_end, exc_info=exc)
        progress.update(data_task, advance=1, total=len(future_to_week), refresh=True)

  _active_procpool = None

  if failures:
    _report_failures_and_exit(failures, total=len(future_to_week))

  if manifest:
    if manifest_file is None:
      # Not an assert: this guard must survive python -O.
      raise RuntimeError("manifest was populated but no manifest file path was provided")
    with manifest_file.open("w") as f:
      dump(manifest, f, indent=2, default=str)
