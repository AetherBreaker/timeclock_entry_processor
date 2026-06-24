if __name__ == "__main__":
  # First party imports
  from sft_ext.logging.init import init_logging

  init_logging()
# else:
#   from rich import get_console

#   RICH_CONSOLE = get_console()

# Standard library imports
from logging import getLogger

# Third party imports
from pandas import DataFrame, read_csv

# First party imports
from timeclock_entry_processor.environment_init_vars import CWD

logger = getLogger(__name__)

EMPLOYEE_INPUT_DIR = CWD / "employee_input"
EMPLOYEE_INPUT_DIR.mkdir(exist_ok=True)  # Ensure the directory exists

MANUAL_EMPLOYEE_LIST_CSV = max(EMPLOYEE_INPUT_DIR.iterdir(), key=lambda f: f.stat().st_mtime)

type EmployeeGroup = str
type EmployeeName = str


def get_employee_info() -> DataFrame:  # sourcery skip: extract-method
  employee_df = read_csv(
    MANUAL_EMPLOYEE_LIST_CSV,
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
