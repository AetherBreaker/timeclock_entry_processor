from logging import getLogger
from pathlib import Path

from pandas import read_csv
from pandas.core.frame import DataFrame
from utils import get_active_progress

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
  with get_active_progress() as progress:
    files = list(EMPLOYEE_GROUPS_INPUT_FOLDER.iterdir())
    with progress.add_task("Reading employee group lists...", total=len(files)) as task_id:
      for csv_file in files:
        group_name = csv_file.stem
        employee_names = read_csv(csv_file, header=0, usecols=[0], dtype=str)
        employee_names = employee_names.iloc[:, 0].tolist()
        employee_group_lists[group_name] = tuple(employee_names)
        progress.update(task_id, advance=1)
  return employee_group_lists


def create_grouped_employee_list() -> DataFrame:
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

    with get_active_progress() as progress:
      with progress.add_task("Assigning employees to groups...", total=len(employee_group_lists)) as task_id:
        # we can safely assume that any given employee will only ever be in one group, so we can break out of the loop once we find a match
        for group_name, employee_names in employee_group_lists.items():
          # ensure that there isn't more than one employee with the same name in the base employee list
          # otherwise we won't know which one to assign to the group
          pass
          with progress.add_task(f"Processing group '{group_name}'...", total=len(employee_names)) as group_task_id:
            for employee_name in employee_names:
              if employee_name.casefold().strip().startswith("charlet".casefold()):
                pass
              matching_employees = employee_df[employee_df["name"] == employee_name]
              # if len(matching_employees) > 1:
              #   raise ValueError(
              #     f"Expected only one employee with the name '{employee_name}', but found {len(matching_employees)}. Found employees: {matching_employees}"
              #   )
              # elif len(matching_employees) == 0:
              #   raise ValueError(f"Expected to find an employee with the name '{employee_name}', but found none")
              employee_df.loc[matching_employees.index, "group"] = group_name
              progress.update(group_task_id, advance=1)
          progress.update(task_id, advance=1)

  # write the updated employee list to a new CSV file for testing
  # output_csv_path = CWD / "grouped_employee_list.csv"
  # employee_df.to_csv(output_csv_path, index=False)

  return employee_df


if __name__ == "__main__":
  create_grouped_employee_list()
