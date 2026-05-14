"""
Employee Time Clock Entry PDF Generator

Reads time clock data from CSV and generates a PDF timeline visualization
showing when each employee was clocked in. Each employee is color-coded,
and data is organized by calendar week (Monday-Sunday).
"""

import pickle
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import date, time, timedelta
from decimal import Decimal
from logging import getLogger
from os import environ
from pathlib import Path
from sys import platform

from employee_info import get_employee_info
from logging_config import configure_logging, configure_multiprocessing_logging
from pandas import DataFrame, concat, read_csv, to_datetime
from pdf_gen import TimelinePDF, start_mp_pdf_gen
from rich.console import Console
from rich_custom import ProgressCustom

environ.setdefault("PYDANTIC_ERRORS_INCLUDE_URL", "false")


logger = getLogger(__name__)


def load_and_parse_data(csv_path: Path) -> DataFrame:
  """Load CSV and parse datetime columns."""
  df = read_csv(csv_path)

  # Filter out summary rows (Grand Totals, empty rows)
  df = df[df["Employee Name"].notna() & (df["Employee Name"] != "")]

  # Parse datetime columns
  df["In Time"] = to_datetime(df["In Time"], format="%m/%d/%Y %I:%M %p")

  # Handle missing Out Time - set to 9 PM on the same day (vectorized)
  df["Out Time"] = df["Out Time"].replace("N/A", None)
  df["Out Time Parsed"] = to_datetime(df["Out Time"], format="%m/%d/%Y %I:%M %p", errors="coerce")

  # For missing Out Time, set to 9 PM on the In Time date (vectorized)
  mask = df["Out Time Parsed"].isna()
  df.loc[mask, "Out Time Parsed"] = df.loc[mask, "In Time"].dt.floor("D") + timedelta(hours=DEFAULT_OUT_TIME.hour)

  # Parse "Time Worked" column to Decimal for precision (vectorized with fallback)
  # Format: "1.74 Hours" -> Decimal('1.74')
  # First, handle NaN/null values by filling them with "0 Hours"
  df["Time Worked"] = df["Time Worked"].fillna("0 Hours")
  df["Hours Worked"] = df["Time Worked"].astype(str).str.replace(" Hours", "").str.strip()
  # Apply still needed for Decimal conversion but much faster with pre-cleaned strings
  df["Hours Worked"] = df["Hours Worked"].apply(
    lambda x: Decimal(x) if x and x not in ("", "nan", "None", "NaN") else Decimal(0)  # type: ignore
  )

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
  all_times = concat([df["In Time"].dt.time, df["Out Time Parsed"].dt.time])

  min_time = all_times.min()
  max_time = all_times.max()

  # Round down to nearest hour for min, up for max
  min_hour = min_time.hour
  max_hour = max_time.hour if max_time.minute == 0 else max_time.hour + 1

  return time(min_hour, 0), time(min(max_hour, 23), 0 if max_hour < 24 else 59)


def process_store_data(
  pickled_pdf_inst: bytes,
  store_number: int,
  store_df: DataFrame,
  employee_id_to_group: dict[str, str],
  output_base: Path,
  proc_pool: ProcessPoolExecutor,
  proc_futures: list[Future],
  update_progress: Callable[[Future], None],
) -> None:

  unique_employees: list[str] = store_df["Employee Name"].unique().tolist()

  weeks = group_by_weeks(store_df)

  min_time, max_time = calculate_time_range(store_df)

  store_output_dir = output_base / f"SFT{store_number:0>3}"
  store_output_dir.mkdir(parents=True, exist_ok=True)

  for (week_start, week_end), week_df in weeks.items():
    pdf_path = store_output_dir / f"{week_end.strftime('%Y-%m-%d')}.pdf"

    fut = proc_pool.submit(
      start_mp_pdf_gen,
      pickled_pdf_inst,
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
    fut.add_done_callback(update_progress)

    proc_futures.append(fut)


if __name__ == "__main__":
  CWD = Path.cwd()

  # Constants
  INPUT_FOLDER = CWD / "input"
  INPUT_FOLDER.mkdir(exist_ok=True)  # Create input folder if it doesn't exist
  OUTPUT_FOLDER = CWD / "output"
  OUTPUT_FOLDER.mkdir(exist_ok=True)  # Create output folder if it doesn't exist

  DEFAULT_OUT_TIME = time(21, 0)  # 9:00 PM
  # Load employee group information
  EMPLOYEE_INFO = get_employee_info()

  # Create a dictionary mapping employee ID to group for O(1) lookups (instead of DataFrame filtering)
  EMPLOYEE_ID_TO_GROUP: dict[str, str] = dict(zip(EMPLOYEE_INFO["id"], EMPLOYEE_INFO["group"]))
  rich_console = Console(
    width=None if platform == "win32" else 160,
    log_time=platform == "win32",
  )
  queues = configure_logging(rich_console, mp=True)
  INPUT_FOLDER.mkdir(exist_ok=True)

  # Find all CSV files in input folder
  csv_files = list(INPUT_FOLDER.glob("*.csv"))

  if not csv_files:
    logger.warning(f"No CSV files found in {INPUT_FOLDER}\nPlease place CSV files in the '{INPUT_FOLDER}' folder.")
    exit()

  logger.info(f"Found {len(csv_files)} CSV file(s) to process")

  # Process each CSV file
  all_data: list[DataFrame] = []
  for csv_file in csv_files:
    df = load_and_parse_data(csv_file)
    all_data.append(df)

  # Combine all data
  combined_df = concat(all_data, ignore_index=True)
  logger.info(
    f"Total entries loaded: {len(combined_df)}\nOverall date range: {combined_df['Date'].min()} to {combined_df['Date'].max()}"
  )

  # Initialize reusable TimelinePDF class pickle
  initial = TimelinePDF()
  pickled_pdf_inst = pickle.dumps(initial, protocol=pickle.HIGHEST_PROTOCOL)

  # Group by store
  stores = combined_df.groupby("Store Number")

  with ProgressCustom(console=rich_console) as progress:
    with progress.add_task("[magenta]Processing weeks...") as data_task:
      with (
        ProcessPoolExecutor(initializer=configure_multiprocessing_logging, initargs=(queues,)) as procpool,  # type: ignore
        ThreadPoolExecutor() as threadpool,
      ):
        proc_futures = []

        def update_completed_progress(future: Future) -> None:
          future.result()
          progress.update(data_task, advance=1, total=len(proc_futures))

        thread_futures = [
          threadpool.submit(
            process_store_data,
            pickled_pdf_inst,
            int(store_number),  # type: ignore
            store_df,
            EMPLOYEE_ID_TO_GROUP,
            OUTPUT_FOLDER,
            procpool,
            proc_futures,
            update_completed_progress,
          )
          for store_number, store_df in stores
        ]
        for future in as_completed(thread_futures):
          future.result()  # Propagate exceptions from threads if any
