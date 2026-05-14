import pickle
from colorsys import hsv_to_rgb
from datetime import date, datetime, time
from functools import partial
from itertools import chain
from logging import getLogger
from pathlib import Path

from employee_info import EmployeeName
from fpdf import FPDF
from pandas import DataFrame

CWD = Path.cwd()
logger = getLogger(__name__)

# Worker-process-local cache for the PDF font template pickle.
# Populated once by init_pdf_worker() so the ~310 KB font blob is transmitted
# via IPC only N_workers times (pool initializer) instead of once per task.
_PDF_TEMPLATE_BYTES: bytes | None = None


type EmployeeColors = dict[EmployeeName, tuple[int, int, int]]
type GroupLabel = str
type GroupColors = dict[GroupLabel, tuple[int, int, int]]


def to_255(r, g, b):
  return (int(r * 255), int(g * 255), int(b * 255))


def truncate_repeating_decimal(value: float) -> str:
  """Format a float to exactly 3 decimal places with 2-digit integer padding.

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

    # Add Roboto Mono fonts (regular and bold only; italic is never used in rendering)
    self.add_font("RobotoMono", "", str(CWD / "RobotoMono-Regular.ttf"))
    self.add_font("RobotoMono", "B", str(CWD / "RobotoMono-Bold.ttf"))

    # Replace per-font lambdas with picklable equivalents so this instance
    # can be serialized and restored without re-reading font files.
    from collections import defaultdict

    for font in self.fonts.values():
      cw: defaultdict = font.cw  # type: ignore[assignment]  # annotated as dict but is defaultdict at runtime
      default_val = cw.default_factory()  # type: ignore[misc]  # default_factory is always set for TTFFont.cw
      cw.default_factory = partial(_cw_constant, default_val)

    # things that need to be precalced to be reused. Gets baked into the pickled instance
    self.GROUP_GROUPS: dict[str, GroupLabel] = {
      "ADMIN": "Office",
      "Default": "Office",
      "District Manager": "District Manager",
      "Franchisee": "District Manager",
      "Manager": "Manager",
      "Office Corp User": "Office",
      "Reporting Office": "Office",
    }
    self.GROUP_COLORS: GroupColors = {
      "Office": to_255(*(hsv_to_rgb(0.0, 0.0, 0.7))),  # Dark grey
      "District Manager": to_255(*(hsv_to_rgb(0, 0.85, 0.65))),  # Red
      "Manager": to_255(*(hsv_to_rgb(0.14, 0.85, 0.85))),
    }
    self.RESERVED_HUES: list[float] = [
      0.0,  # Red District Manager
      0.14,
      # 0.08,  # Orange Manager
      # 0.15,  # Yellow Office
      # 0.29,  # pre reserving green space because green is massive and too similar
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

    # Build inverse map: index/label -> [employees] for O(1) color assignment,
    # replacing the previous O(n²) scan over employee_color_assigned_map per color.
    idx_to_employees: dict[int | str, list[str]] = {}
    for employee, assigned_idx in employee_color_assigned_map.items():
      idx_to_employees.setdefault(assigned_idx, []).append(employee)

    # Separate group labels from numeric employee indices
    numeric_indices = sorted(k for k in idx_to_employees if isinstance(k, int))

    saturation = 0.85
    value = 0.65

    # Generate colors for numeric employee indices, avoiding reserved hues
    if numeric_indices:
      num_colors = len(numeric_indices)
      # num_colors = 7
      hue_threshold = 0.12  # Minimum distance from reserved hues

      # Try to generate hues evenly distributed while avoiding reserved hues
      employee_hues: list[float] = []
      attempts = num_colors * 10  # Oversample to find suitable hues

      for i in range(attempts):
        candidate_hue = i / attempts
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
        r, g, b = hsv_to_rgb(hue, saturation, value)
        col_tuple = to_255(r, g, b)

        for employee in idx_to_employees.get(num_idx, []):
          colors[employee] = col_tuple

    # Assign group colors from GROUP_COLORS
    for group_label, group_employees in idx_to_employees.items():
      if isinstance(group_label, str):
        color = self.GROUP_COLORS[group_label]
        for employee in group_employees:
          colors[employee] = color

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
    self.margin_top = 20  # Space for time axis and header
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

    # Minimum block dimensions to show any label
    min_width_for_label = 15  # Minimum width in mm to show label
    min_height_for_label = 3  # Minimum height in mm to show label

    # Cache for get_string_width results: (text, font_size_pt) -> width in mm.
    # Eliminates redundant fpdf char-width lookups across days and blocks.
    _width_cache: dict[tuple[str, int], float] = {}

    # Pre-group week_df by date once (O(n)) to avoid repeating O(n) boolean
    # filtering inside the day loop (previously O(n * num_days) total).
    date_groups: dict = {d: grp for d, grp in week_df.groupby("Date", sort=False)}

    # Draw daily rows with employee blocks
    for i, day_date in enumerate(dates_in_week):
      row_y = axis_y + i * row_height

      # Group by employee and collect all their time blocks for this day.
      # groupby dict-comprehension avoids row-by-row Python iteration (iterrows overhead).
      employee_blocks: dict[str, list[tuple[datetime, datetime]]] = {
        emp: list(zip(grp["In Time"], grp["Out Time Parsed"]))
        for emp, grp in date_groups[day_date].groupby("Employee Name", sort=False)
      }

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
          # float64 has ample precision for minute-level positions (no Decimal needed)
          in_hour = in_time.hour + in_time.minute / 60.0
          out_hour = out_time.hour + out_time.minute / 60.0

          block_x_start = self.margin_left + (in_hour - start_hour) * pixels_per_hour
          block_width = (out_hour - in_hour) * pixels_per_hour

          # Draw colored rectangle
          self.set_fill_color(*color)
          self.set_draw_color(0, 0, 0)
          self.rect(block_x_start, block_y, block_width, employee_block_height, "FD")

          # Draw time labels at block ends and employee name in center
          if employee_block_height >= min_height_for_label:
            time_font_size = 8
            name_font_size = 8 if employee_block_height < 5 else 9
            time_pad = 1.0
            name_gap = 1.0
            text_y = block_y + employee_block_height / 2

            # Build and measure time labels
            h_in = in_time.hour % 12 or 12
            h_out = out_time.hour % 12 or 12
            in_label = f"{h_in}:{in_time.minute:02d}{'AM' if in_time.hour < 12 else 'PM'}"
            out_label = f"{h_out}:{out_time.minute:02d}{'AM' if out_time.hour < 12 else 'PM'}"

            key_in = (in_label, time_font_size)
            if key_in not in _width_cache:
              self.set_font("RobotoMono", "", time_font_size)
              _width_cache[key_in] = self.get_string_width(in_label)
            in_label_w = _width_cache[key_in]

            key_out = (out_label, time_font_size)
            if key_out not in _width_cache:
              self.set_font("RobotoMono", "", time_font_size)
              _width_cache[key_out] = self.get_string_width(out_label)
            out_label_w = _width_cache[key_out]

            in_shown = in_label_w + time_pad * 2 <= block_width
            out_shown = out_label_w + time_pad * 2 <= block_width

            # Determine available horizontal space for the centered name label.
            # The name center is at block_width/2; it must not cross into either
            # time label region, so compute the usable half-width on each side.
            name_left_bound = (time_pad + in_label_w + name_gap) if in_shown else time_pad
            name_right_bound = (block_width - time_pad - out_label_w - 2 - name_gap) if out_shown else (block_width - time_pad)
            center = block_width / 2
            available_name_width = 2.0 * min(center - name_left_bound, name_right_bound - center)

            # Pick the best-fitting name candidate for this specific block width.
            # Candidate ordering:
            #   1. Firstname Lastname          (full)
            #   2. Firstname L.               (last name to initial)
            #   3. F. Lastname                (first to initial; only when fn is longer than ln)
            #   4. F.L.                       (both initials)
            display_name = ""
            name_text_width = 0.0
            if block_width >= min_width_for_label and available_name_width > 0:
              parts = employee.split(" - ", 1)
              if len(parts) > 1:
                words = parts[1].split()
                if len(words) >= 2:
                  fn, ln = words[0].title(), words[-1].title()
                  candidates: list[str] = [f"{fn} {ln}", f"{fn} {ln[0]}."]
                  if len(fn) > len(ln):
                    candidates.append(f"{fn[0]}. {ln}")
                  candidates.append(f"{fn[0]}.{ln[0]}.")
                elif len(words) == 1:
                  word = words[0].title()
                  candidates = [word, word[:6]]
                else:
                  candidates = []

                for candidate in candidates:
                  key = (candidate, name_font_size)
                  if key not in _width_cache:
                    self.set_font("RobotoMono", "B", name_font_size)
                    _width_cache[key] = self.get_string_width(candidate)
                  if _width_cache[key] <= available_name_width:
                    display_name = candidate
                    name_text_width = _width_cache[key]
                    break

            # Draw time and name labels, each preceded by a black background rect
            lbl_bg_pad = 0  # extra padding around each label background rect
            lbl_h = 4 + lbl_bg_pad * 2
            lbl_bg_y = text_y - 2 - lbl_bg_pad

            if in_shown:
              in_lbl_x = block_x_start + time_pad
              self.set_fill_color(0, 0, 0)
              self.rect(in_lbl_x - lbl_bg_pad, lbl_bg_y, in_label_w + lbl_bg_pad * 2 + 2, lbl_h, "F")
              self.set_font("RobotoMono", "", time_font_size)
              self.set_text_color(255, 255, 255)
              self.set_xy(in_lbl_x, text_y - 2)
              self.cell(in_label_w, 4, in_label, align="L")

            if out_shown:
              out_lbl_x = block_x_start + block_width - out_label_w - time_pad - 2
              self.set_fill_color(0, 0, 0)
              self.rect(out_lbl_x - lbl_bg_pad, lbl_bg_y, out_label_w + lbl_bg_pad * 2 + 2, lbl_h, "F")
              self.set_font("RobotoMono", "", time_font_size)
              self.set_text_color(255, 255, 255)
              self.set_xy(out_lbl_x, text_y - 2)
              self.cell(out_label_w, 4, out_label, align="L")

            if display_name:
              name_lbl_x = block_x_start + block_width / 2 - name_text_width / 2
              self.set_fill_color(0, 0, 0)
              self.rect(name_lbl_x - lbl_bg_pad, lbl_bg_y, name_text_width + lbl_bg_pad * 2 + 2, lbl_h, "F")
              self.set_font("RobotoMono", "B", name_font_size)
              self.set_text_color(255, 255, 255)
              self.set_xy(name_lbl_x, text_y - 2)
              self.cell(name_text_width, 4, display_name, align="L")

            self.set_text_color(0, 0, 0)

    # Calculate total hours for each employee in this week.
    employee_hours: dict[str, float] = {
      str(emp): float(grp["Hours Worked"].sum()) for emp, grp in week_df.groupby("Employee Name", sort=False)
    }

    # Draw legend at bottom with hours
    self.draw_legend(self.margin_left, page_height - self.margin_bottom + 5, employee_hours)

  def draw_legend(self, x: float, y: float, employee_hours: dict[str, float] = None):
    # sourcery skip: extract-duplicate-method
    """Draw employee color legend with total hours and group color legend."""
    if employee_hours is None:
      employee_hours = {}

    self.set_font("RobotoMono", "B", 9)
    self.set_xy(x, y)
    self.cell(50, 5, "Employees:", align="L")

    self.set_font("RobotoMono", "B", 8)
    legend_y = y + 5  # Reduced from 6 to save space

    sorted_employees = sorted(filter(lambda e: employee_hours.get(e, 0.0) > 0, self.employee_colors.keys()))

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
      total_hours = employee_hours.get(employee, 0.0) or 0.0

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
      self.set_font("RobotoMono", "B", 8)

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


def init_pdf_worker(logging_queues, pickled_bytes: bytes) -> None:
  """ProcessPoolExecutor initializer: cache the PDF font template and configure logging.

  Called once per worker process so the font template is transmitted via IPC
  only N_workers times (not once per task), eliminating repeated copies of the
  ~310 KB font blob across all store-week tasks.
  """
  from logging_config import configure_multiprocessing_logging

  configure_multiprocessing_logging(logging_queues)
  global _PDF_TEMPLATE_BYTES
  _PDF_TEMPLATE_BYTES = pickled_bytes


def start_mp_pdf_gen(
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
  pdf: TimelinePDF = pickle.loads(_PDF_TEMPLATE_BYTES)  # type: ignore[arg-type]
  pdf.configure(unique_employees, employee_id_to_group, store_number)
  pdf.render_week(week_start, week_end, week_df, min_time, max_time)
  pdf.output(str(pdf_path))
  logger.info(
    f"""Finished {store_number} - {week_start} to {week_end}
    Saved to: {pdf_path}"""
  )


if __name__ == "__main__":
  import pickle
  from datetime import time, timedelta
  from logging import getLogger
  from pathlib import Path
  from sys import platform

  from employee_info import get_employee_info
  from logging_config import configure_logging
  from pandas import DataFrame, concat, read_csv, to_datetime, to_numeric
  from pdf_gen import TimelinePDF
  from rich.console import Console

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

  DEFAULT_OUT_TIME = time(21, 0)

  def _load(csv_path: Path) -> DataFrame:
    df = read_csv(csv_path)
    df = df[df["Employee Name"].notna() & (df["Employee Name"] != "")]
    df["In Time"] = to_datetime(df["In Time"], format="%m/%d/%Y %I:%M %p")
    df["Out Time"] = df["Out Time"].replace("N/A", None)
    df["Out Time Parsed"] = to_datetime(df["Out Time"], format="%m/%d/%Y %I:%M %p", errors="coerce")
    mask = df["Out Time Parsed"].isna()
    df.loc[mask, "Out Time Parsed"] = df.loc[mask, "In Time"].dt.floor("D") + timedelta(hours=DEFAULT_OUT_TIME.hour)
    df["Hours Worked"] = to_numeric(
      df["Time Worked"].fillna("0 Hours").astype(str).str.replace(" Hours", "").str.strip(),
      errors="coerce",
    ).fillna(0.0)
    df["Date"] = df["In Time"].dt.date
    df["Store Number"] = df["Store"].astype(str).str.split(" - ").str[0].str.strip().astype(int)
    df["Store Number"] = df["Store Number"].where(df["Store"].notna() & df["Store"].astype(str).str.contains(" - "), "Unknown")
    return df

  def _group_by_weeks(df: DataFrame) -> dict[tuple, DataFrame]:
    weeks: dict = {}
    for dt in df["Date"].unique():
      week_start = dt - timedelta(days=dt.weekday())
      week_end = week_start + timedelta(days=6)
      weeks.setdefault((week_start, week_end), []).append(dt)
    return {k: df[df["Date"].isin(v)] for k, v in sorted(weeks.items())}

  def _time_range(df: DataFrame) -> tuple[time, time]:
    all_times = concat([df["In Time"].dt.time, df["Out Time Parsed"].dt.time])
    min_h = all_times.min().hour
    max_h = all_times.max().hour + (0 if all_times.max().minute == 0 else 1)
    return time(min_h, 0), time(min(max_h, 23), 0 if max_h < 24 else 59)

  input_folder = CWD / "input"
  csv_files = list(input_folder.glob("*.csv"))
  if not csv_files:
    raise FileNotFoundError(f"No CSV files found in {input_folder}")

  combined_df = concat([_load(f) for f in csv_files], ignore_index=True)

  employee_info = get_employee_info()
  employee_id_to_group: dict[str, str] = dict(zip(employee_info["id"], employee_info["group"]))

  # Pick the first store and first week
  first_store_number, first_store_df = next((storenum, df) for storenum, df in combined_df.groupby("Store Number") if storenum == 19)
  first_week_key, first_week_df = list(iter(_group_by_weeks(first_store_df).items()))[0]
  week_start, week_end = first_week_key

  unique_employees = first_store_df["Employee Name"].unique().tolist()
  min_time, max_time = _time_range(first_store_df)

  output_path = CWD / "output" / f"TEST_SFT{int(first_store_number):0>3}_{week_end.strftime('%Y-%m-%d')}.pdf"  # type: ignore
  output_path.parent.mkdir(parents=True, exist_ok=True)

  pdf = TimelinePDF()
  pdf.configure(unique_employees, employee_id_to_group, int(first_store_number))  # type: ignore
  pdf.render_week(week_start, week_end, first_week_df, min_time, max_time)
  pdf.output(str(output_path))
  logger.info(f"Test PDF saved to: {output_path}")
