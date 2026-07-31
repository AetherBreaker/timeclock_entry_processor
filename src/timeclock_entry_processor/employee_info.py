if __name__ == "__main__":
  # First party imports
  from aeth_ext import initialize

  initialize()
# else:
#   from rich import get_console

#   RICH_CONSOLE = get_console()

# Standard library imports
from logging import getLogger
from typing import TYPE_CHECKING

# Third party imports
from pandas import DataFrame, read_csv

# First party imports
from timeclock_entry_processor.environment_init_vars import CWD

if TYPE_CHECKING:
  # Standard library imports
  from pathlib import Path

logger = getLogger(__name__)

EMPLOYEE_INPUT_DIR = CWD / "employee_input"
EMPLOYEE_INPUT_DIR.mkdir(exist_ok=True)  # Ensure the directory exists

type EmployeeGroup = str
type EmployeeName = str

_employee_data_path: Path | None = None


def _latest_manual_employee_list_csv() -> Path:
  """Return the most recently modified file in EMPLOYEE_INPUT_DIR.

  Resolved lazily (rather than at import time) since merely importing this
  module — e.g. to inspect logging config from another process — must not
  fail just because EMPLOYEE_INPUT_DIR happens to be empty in that context.
  """
  if _employee_data_path is None:
    try:
      return max(EMPLOYEE_INPUT_DIR.iterdir(), key=lambda f: f.stat().st_mtime)
    except ValueError:
      raise FileNotFoundError(f"No files found in {EMPLOYEE_INPUT_DIR}") from None

  else:
    return _employee_data_path


def get_employee_info() -> DataFrame:  # sourcery skip: extract-method
  employee_df = read_csv(
    _latest_manual_employee_list_csv(),
    header=0,
    names=[
      "id",
      "First Name",
      "Last Name",
      "Status",
      "Manager",
      "Hire Date",
      "group",
      "Work Type",
      "Notes",
      "Created",
      "Updated",
    ],
    usecols=["id", "First Name", "Last Name", "group"],
    dtype=str,
  )
  employee_df["name"] = employee_df["First Name"].str.strip() + " " + employee_df["Last Name"].str.strip()
  employee_df = employee_df[["id", "name", "group"]]

  return employee_df


if __name__ == "__main__":
  get_employee_info()
