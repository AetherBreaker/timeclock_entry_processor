"""
Employee Time Clock Entry PDF Generator

Reads time clock data from CSV and generates a PDF timeline visualization
showing when each employee was clocked in. Each employee is color-coded,
and data is organized by calendar week (Monday-Sunday).
"""

import colorsys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
from fpdf import FPDF

# Constants
CSV_FILE = "Time-Clock-Entry-Report.csv"
OUTPUT_FILE = "employee-timeline.pdf"
DEFAULT_OUT_TIME = time(21, 0)  # 9:00 PM


def load_and_parse_data(csv_path: Path) -> pd.DataFrame:
  """Load CSV and parse datetime columns."""
  df = pd.read_csv(csv_path)

  # Filter out summary rows (Grand Totals, empty rows)
  df = df[df["Employee Name"].notna() & (df["Employee Name"] != "")]

  # Parse datetime columns
  df["In Time"] = pd.to_datetime(df["In Time"], format="%m/%d/%Y %I:%M %p")

  # Handle missing Out Time - set to 9 PM on the same day
  df["Out Time"] = df["Out Time"].apply(lambda x: x if pd.notna(x) and x != "N/A" else None)
  df["Out Time Parsed"] = pd.to_datetime(df["Out Time"], format="%m/%d/%Y %I:%M %p", errors="coerce")

  # For missing Out Time, set to 9 PM on the In Time date
  mask = df["Out Time Parsed"].isna()
  df.loc[mask, "Out Time Parsed"] = df.loc[mask, "In Time"].apply(lambda dt: datetime.combine(dt.date(), DEFAULT_OUT_TIME))

  # Extract date for grouping
  df["Date"] = df["In Time"].dt.date

  return df


def generate_employee_colors(employees: List[str]) -> Dict[str, Tuple[int, int, int]]:
  """Generate distinct colors for each employee using HSV color space."""
  num_employees = len(employees)
  colors = {}

  for i, employee in enumerate(sorted(employees)):
    # Distribute hues evenly around the color wheel
    hue = i / num_employees
    # Use high saturation and medium-high value for vibrant, distinguishable colors
    saturation = 0.7
    value = 0.9

    # Convert HSV to RGB (0-1 range) then to 0-255
    r, g, b = colorsys.hsv_to_rgb(hue, saturation, value)
    colors[employee] = (int(r * 255), int(g * 255), int(b * 255))

  return colors


def group_by_weeks(df: pd.DataFrame) -> Dict[Tuple, pd.DataFrame]:
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


def calculate_time_range(df: pd.DataFrame) -> Tuple[time, time]:
  """Calculate the min and max times across all data for auto-fitted axis."""
  all_times = pd.concat([df["In Time"].dt.time, df["Out Time Parsed"].dt.time])

  min_time = all_times.min()
  max_time = all_times.max()

  # Round down to nearest hour for min, up for max
  min_hour = min_time.hour
  max_hour = max_time.hour if max_time.minute == 0 else max_time.hour + 1

  return time(min_hour, 0), time(min(max_hour, 23), 0 if max_hour < 24 else 59)


class TimelinePDF(FPDF):
  """Custom PDF class for rendering employee timelines."""

  def __init__(self, employee_colors: Dict[str, Tuple[int, int, int]]):
    super().__init__(orientation="L", unit="mm", format="A4")
    self.employee_colors = employee_colors
    self.set_auto_page_break(False)

  def render_week(self, week_start, week_end, week_df: pd.DataFrame, min_time: time, max_time: time):
    """Render a single week's timeline."""
    self.add_page()

    # Page dimensions
    page_width = self.w
    page_height = self.h

    # Margins and layout
    margin_left = 50
    margin_right = 60  # Extra space for legend
    margin_top = 20
    margin_bottom = 10

    # Timeline area
    timeline_width = page_width - margin_left - margin_right
    timeline_height = page_height - margin_top - margin_bottom - 15  # 15 for header

    # Header
    self.set_font("Helvetica", "B", 16)
    self.set_xy(margin_left, margin_top)
    self.cell(timeline_width, 10, f"Week of {week_start.strftime('%B %d, %Y')} - {week_end.strftime('%B %d, %Y')}", align="L")

    # Calculate time axis parameters
    start_hour = min_time.hour
    end_hour = max_time.hour + 1  # Include the end hour
    total_hours = end_hour - start_hour
    pixels_per_hour = timeline_width / total_hours

    # Axis position
    axis_y = margin_top + 15

    # Draw time axis
    self.set_font("Helvetica", "", 8)
    self.set_draw_color(0, 0, 0)
    self.line(margin_left, axis_y, margin_left + timeline_width, axis_y)

    for hour in range(start_hour, end_hour + 1):
      x = margin_left + (hour - start_hour) * pixels_per_hour
      self.line(x, axis_y - 2, x, axis_y + 2)

      # Time label
      hour_12 = hour % 12
      if hour_12 == 0:
        hour_12 = 12
      am_pm = "AM" if hour < 12 else "PM"
      label = f"{hour_12}{am_pm}"
      self.set_xy(x - 5, axis_y + 2)
      self.cell(10, 4, label, align="C")

    # Draw vertical grid lines for each hour
    self.set_draw_color(220, 220, 220)  # Light grey
    for hour in range(start_hour, end_hour + 1):
      x = margin_left + (hour - start_hour) * pixels_per_hour
      self.line(x, axis_y, x, axis_y + timeline_height)

    # Get dates in this week (sorted)
    dates_in_week = sorted(week_df["Date"].unique())

    # Calculate row height
    row_height = timeline_height / max(len(dates_in_week), 1)
    block_height = min(row_height * 0.6, 15)  # Block height within row

    # Draw daily rows
    self.set_font("Helvetica", "", 9)

    for i, date in enumerate(dates_in_week):
      row_y = axis_y + 10 + i * row_height

      # Date label
      day_name = date.strftime("%A")
      date_str = date.strftime("%m/%d/%Y")
      self.set_xy(10, row_y + row_height / 2 - 3)
      self.cell(35, 6, f"{day_name}\n{date_str}", align="R")

      # Horizontal gridline
      self.set_draw_color(200, 200, 200)
      self.line(margin_left, row_y, margin_left + timeline_width, row_y)

      # Get entries for this date
      day_data = week_df[week_df["Date"] == date]

      # Group by employee and collect all their time blocks for this day
      employee_blocks = {}
      for _, row in day_data.iterrows():
        employee = row["Employee Name"]
        in_time = row["In Time"]
        out_time = row["Out Time Parsed"]

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
          # Calculate horizontal position
          in_hour = in_time.hour + in_time.minute / 60
          out_hour = out_time.hour + out_time.minute / 60

          block_x_start = margin_left + (in_hour - start_hour) * pixels_per_hour
          block_width = (out_hour - in_hour) * pixels_per_hour

          # Draw colored rectangle
          self.set_fill_color(*color)
          self.set_draw_color(0, 0, 0)
          self.rect(block_x_start, block_y, block_width, employee_block_height, "FD")

          # Add employee name label if block is wide enough
          min_width_for_label = 15  # Minimum width in mm to show label
          if block_width >= min_width_for_label:
            # Extract first and last name from "ID - FIRST LAST" format
            name_parts = employee.split(" - ")
            if len(name_parts) > 1:
              full_name = name_parts[1]
              name_words = full_name.split()

              if len(name_words) >= 2:
                first_name = name_words[0].title()
                last_name = name_words[-1].title()

                # Try full name first
                display_name = f"{first_name} {last_name}"

                # Set font to measure text width
                self.set_text_color(255, 255, 255)
                self.set_font("Helvetica", "B", 7)

                # Check if full name fits (leave small margin)
                text_width = self.get_string_width(display_name)
                available_width = block_width - 2  # 2mm margin

                # If full name doesn't fit, use "First L" format
                if text_width > available_width:
                  last_initial = last_name[0]
                  display_name = f"{first_name} {last_initial}"
              elif len(name_words) == 1:
                # Only one word in name
                display_name = name_words[0].title()
              else:
                display_name = employee
            else:
              display_name = employee

            # Use white text for better contrast on colored blocks
            self.set_text_color(255, 255, 255)
            self.set_font("Helvetica", "B", 7)

            # Center text in the block
            text_x = block_x_start + block_width / 2
            text_y = block_y + employee_block_height / 2 - 1
            self.set_xy(text_x - block_width / 2, text_y)
            self.cell(block_width, employee_block_height, display_name, align="C")

            # Reset text color to black
            self.set_text_color(0, 0, 0)

    # Draw legend
    self.draw_legend(margin_left + timeline_width + 5, axis_y + 10)

  def draw_legend(self, x: float, y: float):
    """Draw employee color legend."""
    self.set_font("Helvetica", "B", 10)
    self.set_xy(x, y)
    self.cell(50, 5, "Employees:", align="L")

    self.set_font("Helvetica", "", 7)
    legend_y = y + 7

    sorted_employees = sorted(self.employee_colors.keys())

    for i, employee in enumerate(sorted_employees):
      color = self.employee_colors[employee]

      # Color swatch
      self.set_fill_color(*color)
      self.set_draw_color(0, 0, 0)
      self.rect(x, legend_y + i * 5, 3, 3, "FD")

      # Employee name (truncate if too long)
      name = employee
      if len(name) > 20:
        name = name[:17] + "..."

      self.set_xy(x + 4, legend_y + i * 5)
      self.cell(50, 3, name, align="L")


def main():
  """Main entry point."""
  # Paths
  base_path = Path(__file__).parent.parent
  csv_path = base_path / CSV_FILE
  output_path = base_path / OUTPUT_FILE

  print(f"Loading data from {csv_path}...")
  df = load_and_parse_data(csv_path)

  print(f"Loaded {len(df)} entries")
  print(f"Date range: {df['Date'].min()} to {df['Date'].max()}")

  # Get unique employees and generate colors
  unique_employees = df["Employee Name"].unique().tolist()
  print(f"Found {len(unique_employees)} unique employees")

  employee_colors = generate_employee_colors(unique_employees)

  # Group by weeks
  weeks = group_by_weeks(df)
  print(f"Data spans {len(weeks)} week(s)")

  # Calculate time range for axis
  min_time, max_time = calculate_time_range(df)
  print(f"Time range: {min_time.strftime('%I:%M %p')} to {max_time.strftime('%I:%M %p')}")

  # Generate PDF
  print(f"\nGenerating PDF...")
  pdf = TimelinePDF(employee_colors)

  for (week_start, week_end), week_df in weeks.items():
    print(f"  Rendering week: {week_start} to {week_end}")
    pdf.render_week(week_start, week_end, week_df, min_time, max_time)

  # Save PDF
  pdf.output(str(output_path))
  print(f"\nPDF saved to: {output_path}")


if __name__ == "__main__":
  main()
