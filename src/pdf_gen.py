import pickle
from colorsys import hsv_to_rgb
from datetime import date, datetime, time
from decimal import Decimal
from functools import partial
from itertools import chain
from logging import getLogger
from pathlib import Path

from employee_info import EmployeeName
from fpdf import FPDF
from pandas import DataFrame

CWD = Path.cwd()
logger = getLogger(__name__)


type EmployeeColors = dict[EmployeeName, tuple[int, int, int]]
type GroupLabel = str
type GroupColors = dict[GroupLabel, tuple[int, int, int]]


def to_255(r, g, b):
  return (int(r * 255), int(g * 255), int(b * 255))


def truncate_repeating_decimal(value: Decimal) -> str:
  """Format a Decimal to exactly 3 decimal places with 2-digit integer padding.

  Examples:
    24.21666666... -> "24.217"
    26.33333... -> "26.333"
    5.5 -> "05.500"

  Returns a string that is always exactly 6 characters long (XX.XXX).
  """
  return f"{value:06.3f}"


def _cw_constant(value):
  """Picklable replacement for the per-font lambda in TTFFont.cw.default_factory."""
  return value


class TimelinePDF(FPDF):
  def __init__(self):
    super().__init__(orientation="L", unit="mm", format="A4")  # Landscape orientation
    self.set_auto_page_break(False)

    # Add Roboto Mono fonts
    self.add_font("RobotoMono", "", str(CWD / "RobotoMono-VariableFont_wght.ttf"))
    self.add_font("RobotoMono", "B", str(CWD / "RobotoMono-VariableFont_wght.ttf"))
    self.add_font("RobotoMono", "I", str(CWD / "RobotoMono-Italic-VariableFont_wght.ttf"))

    # Replace per-font lambdas with picklable equivalents so this instance
    # can be serialized and restored without re-reading font files.
    from collections import defaultdict

    for font in self.fonts.values():
      cw: defaultdict = font.cw  # type: ignore[assignment]  # annotated as dict but is defaultdict at runtime
      default_val = cw.default_factory()  # type: ignore[misc]  # default_factory is always set for TTFFont.cw
      cw.default_factory = partial(_cw_constant, default_val)

    # things that need to be precalced to be reused. Gets baked into the pickled instance
    self.GROUP_GROUPS: dict[str, GroupLabel] = {
      "ADMIN": "Admin",
      "Default": "Admin",
      "District Manager": "District Manager",
      "Franchisee": "District Manager",
      "Manager": "Manager",
      "Office Corp User": "Office",
      "Reporting Office": "Office",
    }
    self.GROUP_COLORS: GroupColors = {
      "Admin": to_255(*(hsv_to_rgb(0.0, 0.0, 0.7))),  # Dark grey
      "District Manager": to_255(*(hsv_to_rgb(0, 0.85, 0.65))),  # Red
      "Manager": to_255(*(hsv_to_rgb(0.08, 0.85, 0.85))),  # Orange
      "Office": to_255(*(hsv_to_rgb(0.15, 0.85, 0.85))),  # Yellow
    }
    self.RESERVED_HUES: list[Decimal] = [
      Decimal("0.0"),  # Red District Manager
      Decimal("0.08"),  # Orange Manager
      Decimal("0.15"),  # Yellow Office
      Decimal("0.29"),  # pre reserving green space because green is massive and too similar
    ]

  def configure(self, unique_employees: list[str], employee_id_to_group: dict[str, str], store_number: int) -> None:
    """Set per-PDF data after loading from the font template pickle."""
    self.employee_colors = self.generate_employee_colors(unique_employees, employee_id_to_group)
    self.store_number = store_number

  def generate_employee_colors(self, employees: list[str], employee_id_to_group: dict[str, str]) -> EmployeeColors:
    # sourcery skip: extract-method, move-assign, use-named-expression
    """Generate distinct colors for each employee using HSV color space.
    Group colors are hard-coded for consistency. Employee colors are generated
    dynamically while avoiding the hues used by group colors.
    """
    colors = {}

    employee_color_assigned_map = {}

    idx = 0

    color_index: set[str | int] = set()

    for employee in sorted(employees):
      # Extract employee ID from "ID - NAME" format
      employee_id = employee.split(" - ")[0].strip() if " - " in employee else employee

      # Use dictionary lookup instead of DataFrame filtering (O(1) instead of O(n))
      employee_group = employee_id_to_group.get(employee_id, "")
      if employee_group in self.GROUP_GROUPS:
        result = self.GROUP_GROUPS[employee_group]
      else:
        result = idx
        idx += 1

      employee_color_assigned_map[employee] = result
      if result != "Admin":
        color_index.add(result)

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
        # Use pre-calculated RESERVED_HUES instead of recalculating
        min_distance = min(
          (min(abs(candidate_hue - hue), 1 - abs(candidate_hue - hue)) for hue in chain(self.RESERVED_HUES, employee_hues)),
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
        colors[employee] = self.GROUP_COLORS[assigned_idx]

    return colors

  def render_week(self, week_start: date, week_end: date, week_df: DataFrame, min_time: time, max_time: time):
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
    self.set_font("RobotoMono", "B", 12)
    self.set_xy(self.margin_left, 5)
    self.cell(
      timeline_width,
      6,
      f"SFT{self.store_number:0>3} - Week of {week_start.strftime('%B %d, %Y')} - {week_end.strftime('%B %d, %Y')}",
      align="C",
    )

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
    self.set_font("RobotoMono", "B", 9)

    # Draw vertical grid lines for each hour
    self.set_draw_color(0, 0, 0)  # Black
    self.line(self.margin_left, axis_y, self.margin_left, axis_y + timeline_height)
    self.set_draw_color(220, 220, 220)  # Light grey
    for hour in range(start_hour + 1, end_hour + 1):
      x = self.margin_left + (hour - start_hour) * pixels_per_hour

      # First and last lines are black, others are light grey
      if hour == end_hour:
        self.set_draw_color(0, 0, 0)  # Black
      self.line(x, axis_y, x, axis_y + timeline_height)

    date_labels = [date.strftime("%a %m/%d") for date in dates_in_week]
    max_width = max(self.get_string_width(label) for label in date_labels)

    day_label_cell_x_pos = self.margin_left - 10

    day_row_separator_x_start_pos = self.margin_left - 10 - max_width - 2
    day_row_separator_x_stop_pos = self.margin_left + timeline_width

    for i, label in enumerate(date_labels):
      row_y = axis_y + i * row_height

      # Day name and date
      self.set_xy(5, row_y + row_height / 2 - 2)
      self.cell(day_label_cell_x_pos, 4, label, align="R")

      # Horizontal gridline
      # self.set_draw_color(200, 200, 200)
      self.set_draw_color(0, 0, 0)
      self.set_line_width(0.5)
      self.line(day_row_separator_x_start_pos, row_y, day_row_separator_x_stop_pos, row_y)

    # draw last separator at the bottom of the timeline table
    last_separator_y = axis_y + num_days * row_height
    self.line(day_row_separator_x_start_pos, last_separator_y, day_row_separator_x_stop_pos, last_separator_y)

    # side line
    self.line(day_row_separator_x_start_pos, axis_y, day_row_separator_x_start_pos, last_separator_y)

    # Draw horizontal time axis
    self.set_font("RobotoMono", "", 7)
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

    # Add employee name label if block is wide enough
    min_width_for_label = 15  # Minimum width in mm to show label
    min_height_for_label = 3  # Minimum height in mm to show label

    # Draw daily rows with employee blocks
    for i, day_date in enumerate(dates_in_week):
      row_y = axis_y + i * row_height

      # Get entries for this date
      day_data = week_df[week_df["Date"] == day_date]

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
                  self.set_font("RobotoMono", "B", 8)
                else:
                  self.set_font("RobotoMono", "B", 9)

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
                  self.set_font("RobotoMono", "B", 8)
                else:
                  self.set_font("RobotoMono", "B", 9)
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
    self.draw_legend(self.margin_left, page_height - self.margin_bottom + 5, employee_hours)

  def draw_legend(self, x: float, y: float, employee_hours: dict[str, Decimal] = None):
    # sourcery skip: extract-duplicate-method
    """Draw employee color legend with total hours and group color legend."""
    if employee_hours is None:
      employee_hours = {}

    self.set_font("RobotoMono", "B", 9)
    self.set_xy(x, y)
    self.cell(50, 5, "Employees:", align="L")

    self.set_font("RobotoMono", "", 7)
    legend_y = y + 5  # Reduced from 6 to save space

    sorted_employees = sorted(filter(lambda e: employee_hours.get(e, Decimal(0)) > 0, self.employee_colors.keys()))

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
      # Employee name and hours (full name, no truncation)
      # Get total hours for this employee
      total_hours = employee_hours.get(employee, Decimal(0))

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

      # Truncate repeating decimals for display
      hours_display = truncate_repeating_decimal(total_hours)

      # Apply proper casing to employee name
      name_parts = employee.split(" - ")
      if len(name_parts) <= 1:
        raise ValueError(f"Unexpected employee name format: {employee}")

      display_text = f"{name_parts[0].strip(): <7}  {name_parts[1].strip().title(): <35}  {hours_display}h"

      self.set_xy(pos_x + 4, pos_y)
      self.cell(col_width - 4, 3, display_text, align="L")

    # Draw group legend on the right side
    if self.GROUP_COLORS:
      # Position group legend from the right edge
      group_legend_x = page_width - group_legend_width - self.margin_right  # 10mm from right edge

      # Draw separator line on the left of the group legend
      sorted_groups = sorted(self.GROUP_COLORS.keys())
      separator_x = group_legend_x - 2  # 2mm gap before the legend
      separator_y_start = y
      separator_y_end = legend_y + len(sorted_groups) * row_height + 3  # Cover all group items
      self.set_draw_color(0, 0, 0)  # Black
      self.set_line_width(0.5)
      self.line(separator_x, separator_y_start, separator_x, separator_y_end)
      self.set_line_width(0.2)  # Reset to default line width

      # Draw "Groups:" header
      self.set_font("RobotoMono", "B", 9)
      self.set_xy(group_legend_x, separator_y_start)
      self.cell(group_legend_width, 5, "Groups:", align="L")

      # Draw group color swatches
      self.set_font("RobotoMono", "", 7)

      for i, group_name in enumerate(sorted_groups):
        color = self.GROUP_COLORS[group_name]

        group_pos_y = legend_y + i * row_height

        # Color swatch
        self.set_fill_color(*color)
        self.set_draw_color(0, 0, 0)
        self.rect(group_legend_x, group_pos_y, 3, 3, "FD")

        # Group name
        self.set_xy(group_legend_x + 4, group_pos_y)
        self.cell(group_legend_width - 4, 3, group_name, align="L")


def start_mp_pdf_gen(
  pickled_pdf_inst: bytes,
  unique_employees: list[str],
  employee_id_to_group: dict[str, str],
  store_number: int,
  week_start: date,
  week_end: date,
  week_df: DataFrame,
  min_time: time,
  max_time: time,
  pdf_path: Path,
):
  logger.info(
    f"""Processing Store {store_number}
    Date range: {week_start} to {week_end}"""
  )
  pdf: TimelinePDF = pickle.loads(pickled_pdf_inst)
  pdf.configure(unique_employees, employee_id_to_group, store_number)
  pdf.render_week(week_start, week_end, week_df, min_time, max_time)
  pdf.output(str(pdf_path))
  logger.info(
    f"""Finished {store_number} - {week_start} to {week_end}
    Saved to: {pdf_path}"""
  )
