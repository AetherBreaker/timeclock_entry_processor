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
  from sft_ext.logging.init_logging import init_logging

  mp_queue = Queue()

  init_logging(mp_queue)
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
from typing import TYPE_CHECKING, TypedDict

# Third party imports
from pandas import DataFrame, concat, read_csv, to_datetime, to_numeric

# First party imports
from sft_ext.rich.progress import Progress
from timeclock_entry_processor.employee_info import get_employee_info
from timeclock_entry_processor.environment_init_vars import CWD
from timeclock_entry_processor.pdf_gen import TimelinePDF, init_pdf_worker, start_mp_pdf_gen

if TYPE_CHECKING:
  # First party imports
  from sft_ext.logging.logging_bases import FixedLogRecord

logger = getLogger(__name__)


type ProcessResult = list[tuple[list[str], dict[str, str], int, date, date, DataFrame, time, time, Path]]


FONT_INPUT_FOLDER = CWD / "font_input"
if not FONT_INPUT_FOLDER.exists():
  raise FileNotFoundError(f"Font input folder not found at {FONT_INPUT_FOLDER}. Please create it and add necessary font files.")

DEFAULT_OUT_TIME = time(21, 0)  # 9:00 PM
HOURS_PER_DAY = 24
# Load employee group information
EMPLOYEE_INFO = get_employee_info()
# Create a dictionary mapping employee ID to group for O(1) lookups (instead of DataFrame filtering)
EMPLOYEE_ID_TO_GROUP: dict[str, str] = dict(zip(EMPLOYEE_INFO["id"], EMPLOYEE_INFO["group"], strict=False))

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


def main(mp_queue: Queue[FixedLogRecord], input_path: Path, output_folder: Path, manifest_file: Path | None) -> None:
  df = load_and_parse_data(input_path)
  logger.info(f"Total entries loaded: {len(df)}\nOverall date range: {df['Date'].min()} to {df['Date'].max()}")

  # Initialize reusable TimelinePDF class pickle
  initial = TimelinePDF(FONT_INPUT_FOLDER)
  pickled_pdf_inst = pickle.dumps(initial, protocol=pickle.HIGHEST_PROTOCOL)

  # Group by store
  stores = df.groupby("Store Number")

  manifest = None

  if manifest_file is not None:
    manifest = {}

  with Progress(console=RICH_CONSOLE, auto_refresh=False) as progress:
    with progress.add_task("[magenta]Processing weeks...") as data_task:
      with (
        ProcessPoolExecutor(
          # max_workers=1,
          initializer=init_pdf_worker,
          initargs=(mp_queue, pickled_pdf_inst),
        ) as procpool,
        ThreadPoolExecutor(
          # max_workers=1,
        ) as threadpool,
      ):
        proc_futures = []

        def update_completed_progress(future: Future[None]) -> None:
          future.result()
          progress.update(data_task, advance=1, total=len(proc_futures), refresh=True)

        thread_futures = [
          threadpool.submit(
            process_store_data,
            int(store_number),  # type: ignore
            store_df,
            EMPLOYEE_ID_TO_GROUP,
            output_folder,
            manifest,
          )
          for store_number, store_df in stores
        ]
        for future in as_completed(thread_futures):
          result = future.result()
          for week_args in result:
            proc_future = procpool.submit(start_mp_pdf_gen, *week_args)
            proc_future.add_done_callback(update_completed_progress)
            proc_futures.append(proc_future)

  if manifest:
    assert manifest_file is not None
    with manifest_file.open("w") as f:
      dump(manifest, f, indent=2, default=str)
