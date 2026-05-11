"""
Employee Time Clock Entry PDF Generator

Reads time clock data from CSV and generates a PDF timeline visualization
showing when each employee was clocked in. Each employee is color-coded,
and data is organized by calendar week (Monday-Sunday).
"""

from colorsys import hsv_to_rgb, rgb_to_hsv
from datetime import datetime, time, timedelta
from decimal import Decimal
from itertools import chain
from logging import getLogger
from pathlib import Path

from employee_info import EmployeeName, get_employee_info
from fpdf import FPDF
from pandas import DataFrame, concat, notna, read_csv, to_datetime

logger = getLogger(__name__)


# Constants
INPUT_FOLDER = "input"
OUTPUT_FOLDER = "output"
DEFAULT_OUT_TIME = time(21, 0)  # 9:00 PM

# Load employee group information
EMPLOYEE_INFO = get_employee_info()


def load_and_parse_data(csv_path: Path) -> DataFrame:
  """Load CSV and parse datetime columns."""
  df = read_csv(csv_path)

  # Filter out summary rows (Grand Totals, empty rows)
  df = df[df["Employee Name"].notna() & (df["Employee Name"] != "")]

  # Parse datetime columns
  df["In Time"] = to_datetime(df["In Time"], format="%m/%d/%Y %I:%M %p")

  # Handle missing Out Time - set to 9 PM on the same day
  df["Out Time"] = df["Out Time"].apply(lambda x: x if notna(x) and x != "N/A" else None)
  df["Out Time Parsed"] = to_datetime(df["Out Time"], format="%m/%d/%Y %I:%M %p", errors="coerce")

  # For missing Out Time, set to 9 PM on the In Time date
  mask = df["Out Time Parsed"].isna()
  df.loc[mask, "Out Time Parsed"] = df.loc[mask, "In Time"].apply(lambda dt: datetime.combine(dt.date(), DEFAULT_OUT_TIME))

  # Parse "Time Worked" column to Decimal for precision
  # Format: "1.74 Hours" -> Decimal('1.74')
  df["Hours Worked"] = df["Time Worked"].apply(
    lambda x: Decimal(str(x).replace(" Hours", "").strip()) if notna(x) and x != "" else Decimal(0)  # type: ignore
  )

  # Extract date for grouping
  df["Date"] = df["In Time"].dt.date

  # Extract store number from "Store" column
  # Format: "13 - Sweet Fire Tobacco 013" -> "013"
  df["Store Number"] = df["Store"].apply(
    lambda x: x.split(" - ")[0].strip().zfill(3) if notna(x) and " - " in str(x) else "Unknown"  # type: ignore
  )

  return df


type EmployeeColors = dict[EmployeeName, tuple[int, int, int]]
type GroupLabel = str
type GroupColors = dict[GroupLabel, tuple[int, int, int]]


def to_255(r, g, b):
  return (int(r * 255), int(g * 255), int(b * 255))


group_groups: dict[str, GroupLabel] = {
  "ADMIN": "Admin",
  "Default": "Admin",
  "District Manager": "District Manager",
  "Franchisee": "District Manager",
  "Manager": "Manager",
  "Office Corp User": "Office",
  "Reporting Office": "Office",
}

# Hard-coded group colors for consistency across runs
GROUP_COLORS: GroupColors = {
  "Admin": to_255(*(hsv_to_rgb(0.0, 0.0, 0.7))),  # Dark grey
  "District Manager": to_255(*(hsv_to_rgb(0, 0.85, 0.65))),  # Red
  "Manager": to_255(*(hsv_to_rgb(0.08, 0.85, 0.85))),  # Orange
  "Office": to_255(*(hsv_to_rgb(0.15, 0.85, 0.85))),  # Yellow
}


def generate_employee_colors(employees: list[str], employee_info: DataFrame = EMPLOYEE_INFO) -> tuple[EmployeeColors, GroupColors]:
  """Generate distinct colors for each employee using HSV color space.
  Group colors are hard-coded for consistency. Employee colors are generated
  dynamically while avoiding the hues used by group colors.
  """
  colors = {}
  group_colors: GroupColors = GROUP_COLORS.copy()

  employee_color_assigned_map = {}

  idx = 0

  color_index: set[str | int] = set()

  for employee in sorted(employees):
    # Extract employee ID from "ID - NAME" format
    employee_id = employee.split(" - ")[0].strip() if " - " in employee else employee

    lookup_result = employee_info.loc[employee_info["id"] == employee_id, "group"]
    if len(lookup_result) > 0 and lookup_result.iloc[0] in group_groups:
      result = group_groups[lookup_result.iloc[0]]
    else:
      result = idx
      idx += 1

    employee_color_assigned_map[employee] = result
    if result != "Admin":
      color_index.add(result)

  # Calculate reserved hues from group colors (to avoid when generating employee colors)
  reserved_hues: list[Decimal] = [
    Decimal("0.29")  # pre reserving green space because green is massive and too similar
  ]
  for group_name, rgb in GROUP_COLORS.items():
    if group_name != "Admin":  # Admin is grey, no specific hue to avoid
      r, g, b = rgb[0] / 255, rgb[1] / 255, rgb[2] / 255
      h, s, v = rgb_to_hsv(r, g, b)
      reserved_hues.append(Decimal(h))

  # Separate group labels from numeric employee indices
  numeric_indices = sorted([idx for idx in color_index if isinstance(idx, int)])

  saturation = 0.85
  value = 0.65

  # Generate colors for numeric employee indices, avoiding reserved hues
  if numeric_indices:
    num_colors = Decimal(len(numeric_indices))
    hue_threshold = Decimal("0.09")  # Minimum distance from reserved hues

    # Try to generate hues evenly distributed while avoiding reserved hues
    employee_hues: list[Decimal] = []
    attempts = int(num_colors) * 10  # Oversample to find suitable hues

    for i in range(attempts):
      candidate_hue = Decimal(i) / attempts
      # Check distance to all reserved hues (accounting for circular nature of hue)
      min_distance = min(
        (min(abs(candidate_hue - hue), 1 - abs(candidate_hue - hue)) for hue in chain(reserved_hues, employee_hues)),
        default=1.0,
      )
      if min_distance >= hue_threshold:
        employee_hues.append(candidate_hue)

      if len(employee_hues) >= num_colors:
        break

    # If we couldn't find enough distinct hues, fall back to evenly distributed
    while len(employee_hues) < num_colors:
      employee_hues.append(len(employee_hues) / num_colors)

    # Assign generated colors to numeric indices
    for i, num_idx in enumerate(numeric_indices):
      hue = employee_hues[i]
      r, g, b = hsv_to_rgb(float(hue), saturation, value)
      col_tuple = to_255(r, g, b)

      # Assign this color to all employees with this numeric index
      for employee, assigned_idx in employee_color_assigned_map.items():
        if assigned_idx == num_idx:
          colors[employee] = col_tuple

  # Assign group colors from GROUP_COLORS
  for employee, assigned_idx in employee_color_assigned_map.items():
    if isinstance(assigned_idx, str):  # Group label
      colors[employee] = GROUP_COLORS[assigned_idx]

  return colors, group_colors


def group_by_weeks(df: DataFrame) -> dict[tuple, DataFrame]:
  """Group data by calendar weeks (Monday-Sunday)."""
  weeks = {}

  for date in df["Date"].unique():
    # Get the Monday of the week containing this date
    # weekday(): Monday=0, Sunday=6
    days_since_monday = date.weekday()
    week_start = date - timedelta(days=days_since_monday)
    week_end = week_start + timedelta(days=6)

    week_key = (week_start, week_end)

    if week_key not in weeks:
      weeks[week_key] = []

    weeks[week_key].append(date)

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


def truncate_repeating_decimal(value: Decimal) -> str:
  """Truncate a Decimal at the point where digits start repeating.

  Examples:
    24.21666666... -> "24.216"
    26.33333... -> "26.3"
    25.5 -> "25.5"
  """
  str_value = str(value)

  # Split into integer and decimal parts
  if "." not in str_value:
    return str_value

  integer_part, decimal_part = str_value.split(".")

  if not decimal_part:
    return integer_part

  # Look for repeating patterns
  # Check for single repeating digit first (most common: 3333, 6666)
  for i in range(len(decimal_part) - 1):
    if i > 0 and all(c == decimal_part[i] for c in decimal_part[i:]):
      # Found repeating digit at position i
      return f"{integer_part}.{decimal_part[: i + 1]}"

  # Check for longer repeating patterns (e.g., 142857142857)
  for pattern_len in range(1, len(decimal_part) // 2 + 1):
    pattern = decimal_part[:pattern_len]
    # Check if this pattern repeats for the rest of the string
    repeats = True
    for j in range(pattern_len, len(decimal_part), pattern_len):
      chunk = decimal_part[j : j + pattern_len]
      if chunk and chunk != pattern[: len(chunk)]:
        repeats = False
        break

    if repeats and len(decimal_part) > pattern_len * 2:
      # Pattern repeats at least twice
      return f"{integer_part}.{pattern}"

  # No repeating pattern found, return as-is
  return str_value


class TimelinePDF(FPDF):
  """Custom PDF class for rendering employee timelines."""

  def __init__(self, employee_colors: EmployeeColors, group_colors: GroupColors):
    super().__init__(orientation="L", unit="mm", format="A4")  # Landscape orientation
    self.employee_colors = employee_colors
    self.group_colors = group_colors
    self.set_auto_page_break(False)

  def render_week(self, week_start, week_end, week_df: DataFrame, min_time: time, max_time: time):
    """Render a single week's timeline with horizontal time axis."""
    self.add_page()

    # Page dimensions
    page_width = self.w
    page_height = self.h

    # Margins and layout (tightened for more timeline space)
    self.margin_left = 35  # Space for day labels
    self.margin_right = 5
    self.margin_top = 25  # Space for time axis and header
    self.margin_bottom = 35  # Space for legend

    # Timeline area
    timeline_width = page_width - self.margin_left - self.margin_right
    timeline_height = page_height - self.margin_top - self.margin_bottom

    # Header
    self.set_font("Helvetica", "B", 12)
    self.set_xy(self.margin_left, 5)
    self.cell(timeline_width, 6, f"Week of {week_start.strftime('%B %d, %Y')} - {week_end.strftime('%B %d, %Y')}", align="C")

    # Calculate time axis parameters (horizontal)
    start_hour = min_time.hour
    end_hour = max_time.hour + 1  # Include the end hour
    total_hours = end_hour - start_hour
    pixels_per_hour = timeline_width / total_hours

    # Time axis position (top)
    axis_y = self.margin_top

    # Get dates in this week (sorted)
    dates_in_week = sorted(week_df["Date"].unique())
    num_days = len(dates_in_week)

    # Calculate row height for each day
    row_height = timeline_height / max(num_days, 1)
    block_height = min(row_height * 0.9, 40)  # Block height within row (increased for better visibility)

    # Draw day labels on left
    self.set_font("Helvetica", "B", 9)
    for i, date in enumerate(dates_in_week):
      row_y = axis_y + i * row_height
      day_name = date.strftime("%a")  # Short day name (Mon, Tue, etc.)
      date_str = date.strftime("%m/%d")

      # Day name and date
      label = f"{day_name} {date_str}"
      self.set_xy(5, row_y + row_height / 2 - 2)
      self.cell(self.margin_left - 10, 4, label, align="R")

      # Horizontal gridline
      self.set_draw_color(200, 200, 200)
      self.line(self.margin_left, row_y, self.margin_left + timeline_width, row_y)

    # Draw horizontal time axis
    self.set_font("Helvetica", "", 7)
    self.set_draw_color(0, 0, 0)
    self.line(self.margin_left, axis_y, self.margin_left + timeline_width, axis_y)

    for hour in range(start_hour, end_hour + 1):
      x = self.margin_left + (hour - start_hour) * pixels_per_hour
      self.line(x, axis_y - 2, x, axis_y + 2)

      # Time label
      hour_12 = hour % 12
      if hour_12 == 0:
        hour_12 = 12
      am_pm = "AM" if hour < 12 else "PM"
      label = f"{hour_12}{am_pm}"
      self.set_xy(x - 5, axis_y - 8)
      self.cell(10, 4, label, align="C")

    # Draw vertical grid lines for each hour
    self.set_draw_color(220, 220, 220)  # Light grey
    for hour in range(start_hour, end_hour + 1):
      x = self.margin_left + (hour - start_hour) * pixels_per_hour
      self.line(x, axis_y, x, axis_y + timeline_height)

    # Add employee name label if block is wide enough
    min_width_for_label = 15  # Minimum width in mm to show label
    min_height_for_label = 3  # Minimum height in mm to show label

    # Draw daily rows with employee blocks
    for i, date in enumerate(dates_in_week):
      row_y = axis_y + i * row_height

      # Get entries for this date
      day_data = week_df[week_df["Date"] == date]

      # Group by employee and collect all their time blocks for this day
      employee_blocks: dict[str, list[tuple[datetime, datetime]]] = {}
      for _, row in day_data.iterrows():
        employee = row["Employee Name"]
        in_time: datetime = row["In Time"]
        out_time: datetime = row["Out Time Parsed"]

        if employee not in employee_blocks:
          employee_blocks[employee] = []

        employee_blocks[employee].append((in_time, out_time))

      # Draw blocks for each employee (stacked vertically if needed)
      employee_list = sorted(employee_blocks.keys())
      num_employees = len(employee_list)

      for emp_idx, employee in enumerate(employee_list):
        blocks = employee_blocks[employee]
        color = self.employee_colors.get(employee, (128, 128, 128))

        # Calculate vertical position for this employee's blocks
        # Stack employees vertically within the row
        employee_block_height = block_height / max(num_employees, 1)
        block_y = row_y + row_height / 2 - block_height / 2 + emp_idx * employee_block_height

        # Draw all blocks for this employee
        for in_time, out_time in blocks:
          # Calculate horizontal position using Decimal for precision
          in_hour = in_time.hour + Decimal(in_time.minute) / 60
          out_hour = out_time.hour + Decimal(out_time.minute) / 60

          block_x_start = self.margin_left + float(in_hour - start_hour) * pixels_per_hour
          block_width = float(out_hour - in_hour) * pixels_per_hour

          # Draw colored rectangle
          self.set_fill_color(*color)
          self.set_draw_color(0, 0, 0)
          self.rect(block_x_start, block_y, block_width, employee_block_height, "FD")

          if block_width >= min_width_for_label and employee_block_height >= min_height_for_label:
            # Extract first and last name from "ID - FIRST LAST" format
            name_parts = employee.split(" - ")
            if len(name_parts) > 1:
              full_name = name_parts[1]
              name_words = full_name.split()

              if len(name_words) >= 2:
                first_name = name_words[0].title()
                last_name = name_words[-1].title()

                # Set font to measure text width (use larger font for taller blocks)
                if employee_block_height < 5:
                  self.set_font("Helvetica", "B", 8)
                else:
                  self.set_font("Helvetica", "B", 9)

                # Try full first name + full last name
                display_name = f"{first_name} {last_name}"
                text_width = self.get_string_width(display_name)

                # If doesn't fit, try full first name + last initial
                if text_width > block_width - 2:  # 2mm padding
                  display_name = f"{first_name} {last_name[0]}."
                  text_width = self.get_string_width(display_name)

                  # If still doesn't fit, use both initials
                if text_width > block_width - 2:
                  display_name = f"{first_name[0]}.{last_name[0]}."
                  text_width = self.get_string_width(display_name)

                # If even initials don't fit, give up
                if text_width > block_width - 2:
                  display_name = ""
              elif len(name_words) == 1:
                # Only one word in name
                display_name = name_words[0].title()
                # Check if it fits
                if employee_block_height < 5:
                  self.set_font("Helvetica", "B", 8)
                else:
                  self.set_font("Helvetica", "B", 9)
                text_width = self.get_string_width(display_name)
                if text_width > block_width - 2:
                  display_name = display_name[:6]  # Truncate if needed
                  text_width = self.get_string_width(display_name)  # Recalculate for truncated name
              else:
                display_name = ""
            else:
              display_name = ""

            if display_name:
              # Use white text for better contrast on colored blocks
              self.set_text_color(255, 255, 255)

              # Font already set above during width calculation
              # Calculate center position for text (no rotation needed for horizontal blocks)
              text_x = block_x_start + block_width / 2
              text_y = block_y + employee_block_height / 2

              # Get final text width for the display_name we're actually using
              text_width = self.get_string_width(display_name)
              # Draw text centered in the block (horizontally, no rotation)
              self.set_xy(text_x - text_width / 2, text_y - 2)
              self.cell(text_width, 4, display_name, align="L")

              # Reset text color to black
              self.set_text_color(0, 0, 0)

    # Calculate total hours for each employee in this week
    # Sum the "Hours Worked" column (already in Decimal) for each employee
    employee_hours = {}
    for _, row in week_df.iterrows():
      employee = row["Employee Name"]
      hours_worked = row["Hours Worked"]

      if employee not in employee_hours:
        employee_hours[employee] = Decimal(0)
      employee_hours[employee] += hours_worked

    # Draw legend at bottom with hours
    self.draw_legend(self.margin_left, page_height - self.margin_bottom + 10, employee_hours)

  def draw_legend(self, x: float, y: float, employee_hours: dict[str, Decimal] = None):
    # sourcery skip: extract-duplicate-method
    """Draw employee color legend with total hours and group color legend."""
    if employee_hours is None:
      employee_hours = {}

    self.set_font("Helvetica", "B", 9)
    self.set_xy(x, y)
    self.cell(50, 5, "Employees:", align="L")

    self.set_font("Helvetica", "", 7)
    legend_y = y + 5  # Reduced from 6 to save space

    sorted_employees = sorted(self.employee_colors.keys())

    # Calculate maximum rows that fit on page
    page_height = self.h
    available_height = page_height - legend_y - 3  # Reduced margin at bottom
    row_height = 5  # Increased from 4 for better spacing
    max_rows = int(available_height / row_height)

    # Calculate minimum columns needed to fit all employees
    num_employees = len(sorted_employees)
    min_cols_needed = -(-num_employees // max_rows)  # Ceiling division

    # Use the minimum columns needed to prevent overflow
    num_cols = max(min_cols_needed, 1)

    # Calculate space needed for group legend on right side
    page_width = self.w
    group_legend_width = 40  # Fixed width for group legend (groups have short names)
    group_legend_gap = 5  # Gap between employee and group legends

    # Calculate column width based on available space minus group legend space
    available_width = page_width - x - 10 - group_legend_width - group_legend_gap - self.margin_right  # 10mm padding on right side
    col_width = available_width / num_cols

    # Draw employee legend
    for i, employee in enumerate(sorted_employees):
      color = self.employee_colors[employee]

      # Calculate position in grid layout
      col = i % num_cols
      row = i // num_cols

      pos_x = x + col * col_width
      pos_y = legend_y + row * row_height

      # Color swatch
      self.set_fill_color(*color)
      self.set_draw_color(0, 0, 0)
      self.rect(pos_x, pos_y, 3, 3, "FD")

      # Employee name and hours (full name, no truncation)
      # Get total hours for this employee
      total_hours = employee_hours.get(employee, Decimal(0))

      # Truncate repeating decimals for display
      hours_display = truncate_repeating_decimal(total_hours)

      # Apply proper casing to employee name
      name_parts = employee.split(" - ")
      if len(name_parts) > 1:
        # Format: "ID - FIRST LAST" -> "ID - First Last"
        formatted_name = f"{name_parts[0]} - {name_parts[1].title()}"
      else:
        formatted_name = employee.title()

      display_text = f"{formatted_name} ({hours_display}h)"

      self.set_xy(pos_x + 4, pos_y)
      self.cell(col_width - 4, 3, display_text, align="L")

    # Draw group legend on the right side
    if self.group_colors:
      # Position group legend from the right edge
      group_legend_x = page_width - group_legend_width - self.margin_right  # 10mm from right edge

      # Draw "Groups:" header
      self.set_font("Helvetica", "B", 9)
      self.set_xy(group_legend_x, y)
      self.cell(group_legend_width, 5, "Groups:", align="L")

      # Draw group color swatches
      self.set_font("Helvetica", "", 7)
      sorted_groups = sorted(self.group_colors.keys())

      for i, group_name in enumerate(sorted_groups):
        color = self.group_colors[group_name]

        group_pos_y = legend_y + i * row_height

        # Color swatch
        self.set_fill_color(*color)
        self.set_draw_color(0, 0, 0)
        self.rect(group_legend_x, group_pos_y, 3, 3, "FD")

        # Group name
        self.set_xy(group_legend_x + 4, group_pos_y)
        self.cell(group_legend_width - 4, 3, group_name, align="L")


def process_store_data(store_number: str, store_df: DataFrame, output_base: Path, employee_group_df: DataFrame | None = None) -> None:
  """Process data for a single store and generate PDFs by week."""
  print(f"\n  Processing Store {store_number}:")
  print(f"    {len(store_df)} entries")
  print(f"    Date range: {store_df['Date'].min()} to {store_df['Date'].max()}")

  # Get unique employees for this store and generate colors
  unique_employees = store_df["Employee Name"].unique().tolist()
  print(f"    {len(unique_employees)} unique employees")

  employee_colors, group_colors = generate_employee_colors(unique_employees)

  # Group by weeks
  weeks = group_by_weeks(store_df)
  print(f"    {len(weeks)} week(s)")

  # Calculate time range for axis
  min_time, max_time = calculate_time_range(store_df)

  # Create output directory for this store
  store_output_dir = output_base / store_number
  store_output_dir.mkdir(parents=True, exist_ok=True)

  # Generate one PDF per week
  for (week_start, week_end), week_df in weeks.items():
    # Create PDF filename: week ending date
    pdf_filename = f"{week_end.strftime('%Y-%m-%d')}.pdf"
    pdf_path = store_output_dir / pdf_filename

    print(f"    Rendering week {week_start} to {week_end} -> {pdf_filename}")

    pdf = TimelinePDF(employee_colors, group_colors)
    pdf.render_week(week_start, week_end, week_df, min_time, max_time)
    pdf.output(str(pdf_path))


def main():
  """Main entry point."""
  # Paths
  base_path = Path(__file__).parent.parent
  input_folder = base_path / INPUT_FOLDER
  output_folder = base_path / OUTPUT_FOLDER

  # Create input folder if it doesn't exist
  input_folder.mkdir(exist_ok=True)

  # Find all CSV files in input folder
  csv_files = list(input_folder.glob("*.csv"))

  if not csv_files:
    print(f"No CSV files found in {input_folder}")
    print(f"Please place CSV files in the '{INPUT_FOLDER}' folder.")
    return

  print(f"Found {len(csv_files)} CSV file(s) to process\n")

  # Process each CSV file
  all_data: list[DataFrame] = []
  for csv_file in csv_files:
    print(f"Loading {csv_file.name}...")
    df = load_and_parse_data(csv_file)
    all_data.append(df)
    print(f"  Loaded {len(df)} entries")

  # Combine all data
  combined_df = concat(all_data, ignore_index=True)
  print(f"\nTotal entries loaded: {len(combined_df)}")
  print(f"Overall date range: {combined_df['Date'].min()} to {combined_df['Date'].max()}")

  # Group by store
  stores = combined_df.groupby("Store Number")
  print(f"\nProcessing {len(stores)} store(s)...")

  # Process each store
  for store_number, store_df in stores:
    process_store_data(str(store_number), store_df, output_folder, EMPLOYEE_INFO)

  print(f"\n✓ All PDFs saved to: {output_folder}")


if __name__ == "__main__":
  main()
