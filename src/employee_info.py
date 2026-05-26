if __name__ == "__main__":
  from sft_ext.logging_ext.init_logging import init_logging

  init_logging()
# else:
#   from rich import get_console

#   RICH_CONSOLE = get_console()

from logging import getLogger

from environment_init_vars import CWD
from pandas import read_csv
from pandas.core.frame import DataFrame

logger = getLogger(__name__)


EMPLOYEE_GROUPS_INPUT_FOLDER = CWD / "employee_groups_input"
EMPLOYEE_GROUPS_INPUT_FOLDER.mkdir(exist_ok=True)


MANUAL_EMPLOYEE_LIST_CSV = max((CWD / "employee_list_input").iterdir(), key=lambda f: f.stat().st_mtime)

type EmployeeGroup = str
type EmployeeName = str


def get_employee_info() -> DataFrame:  # sourcery skip: extract-method
  # if EMPLOYEE_GROUPS_INPUT_FOLDER.exists():
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
