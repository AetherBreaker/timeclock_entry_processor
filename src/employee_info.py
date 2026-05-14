from logging import getLogger
from pathlib import Path

from pandas import read_csv
from pandas.core.frame import DataFrame

logger = getLogger(__name__)

CWD = Path.cwd()

EMPLOYEE_GROUPS_INPUT_FOLDER = CWD / "employee_groups_input"
EMPLOYEE_GROUPS_INPUT_FOLDER.mkdir(exist_ok=True)

# This should be the newest file in the employee_list_input folder
BASE_EMPLOYEE_LIST_CSV = max((CWD / "auto_employee_list_input").iterdir(), key=lambda f: f.stat().st_mtime)

MANUAL_EMPLOYEE_LIST_CSV = max((CWD / "employee_list_input").iterdir(), key=lambda f: f.stat().st_mtime)

type EmployeeGroup = str
type EmployeeName = str


def get_employee_group_lists() -> dict[EmployeeGroup, tuple[EmployeeName]]:
  employee_group_lists = {}
  files = list(EMPLOYEE_GROUPS_INPUT_FOLDER.iterdir())
  for csv_file in files:
    group_name = csv_file.stem
    employee_names = read_csv(csv_file, header=0, usecols=[0], dtype=str)
    employee_names = employee_names.iloc[:, 0].tolist()
    employee_group_lists[group_name] = tuple(employee_names)
  return employee_group_lists


def get_employee_info() -> DataFrame:  # sourcery skip: extract-method
  if EMPLOYEE_GROUPS_INPUT_FOLDER.exists():
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
  else:
    # Read the base employee list CSV file
    employee_df = read_csv(
      BASE_EMPLOYEE_LIST_CSV,
      header=0,
      names=[
        "id",
        "name",
        "Phone",
        "Emergency Contact",
        "Emergency Phone",
        "Hire Date",
        "DOB",
      ],
      usecols=["id", "name"],
      dtype=str,
    )

    # add an empty column for the employee group
    employee_df["group"] = ""

    # Get the employee group lists
    employee_group_lists = get_employee_group_lists()

    # Build a name-to-group mapping dictionary (more efficient than nested loops with DataFrame filtering)
    name_to_group_map = {}
    for group_name, employee_names in employee_group_lists.items():
      for employee_name in employee_names:
        name_to_group_map[employee_name] = group_name

    # Vectorized assignment using map
    employee_df["group"] = employee_df["name"].map(name_to_group_map).fillna("")

  # write the updated employee list to a new CSV file for testing
  # output_csv_path = CWD / "grouped_employee_list.csv"
  # employee_df.to_csv(output_csv_path, index=False)

  return employee_df


if __name__ == "__main__":
  get_employee_info()
