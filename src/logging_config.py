from __future__ import annotations

import atexit
import logging
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from queue import Queue
from sys import platform
from time import gmtime, localtime, strftime, time
from typing import TYPE_CHECKING

from environment_init_vars import SETTINGS
from rich.console import Console
from rich.logging import RichHandler
from rich.traceback import install

if TYPE_CHECKING:
  from typing import Literal

  from rich.console import ConsoleRenderable
  from rich.traceback import Traceback


RICH_CONSOLE = Console(
  width=None if platform == "win32" else 160,
  log_time=platform == "win32",
)

install(show_locals=True)

CWD = Path.cwd()


PROJECT_NAME = "timeclockentryprocessor"

max_width = 36


LOG_LOC_FOLDER = SETTINGS.persisted_dir_loc / "logs"
LOG_LOC_FOLDER.mkdir(exist_ok=True, parents=True)

MAX_WIDTH_FILE = LOG_LOC_FOLDER / "max_width.txt"

LOGGING_TIMESTAMP_FORMAT = "%b, %d %a %I:%M %p"


class FixedRichHandler(RichHandler):
  def render(
    self,
    *,
    record: logging.LogRecord,
    traceback: Traceback | None,
    message_renderable: ConsoleRenderable,
  ) -> ConsoleRenderable:
    """Render log for display.

    Args:
        record (LogRecord): logging Record.
        traceback (Traceback | None): Traceback instance or None for no Traceback.
        message_renderable (ConsoleRenderable): Renderable (typically Text) containing log message contents.

    Returns:
        ConsoleRenderable: Renderable to display log.
    """

    pathpath = Path(record.pathname)

    if "site-packages" in pathpath.parts:
      libname_index = pathpath.parts.index("site-packages") + 1
    elif PROJECT_NAME in pathpath.parts:
      libname_index = pathpath.parts.index(PROJECT_NAME)
    elif "src" in pathpath.parts:
      libname_index = pathpath.parts.index("src")
    elif "Lib" in pathpath.parts:
      libname_index = pathpath.parts.index("Lib") + 1
    else:
      libname_index = 0

    path = ".".join(pathpath.parts[libname_index:])
    if "src." in path:
      path = path.split("src.", 1)[1]

    level = self.get_level_text(record)
    time_format = None if self.formatter is None else self.formatter.datefmt
    log_time = datetime.fromtimestamp(record.created)

    return self._log_render(
      self.console,
      [message_renderable, traceback] if traceback else [message_renderable],
      log_time=log_time,
      time_format=time_format,
      level=level,
      path=path,
      line_no=record.lineno,
      link_path=record.pathname if self.enable_link_path else None,
    )


class FixedLogRecord(logging.LogRecord):
  def __init__(self, *args, **kwargs):
    global max_width
    pathpath = Path(args[2])

    if "site-packages" in pathpath.parts:
      libname_index = pathpath.parts.index("site-packages") + 1
      libname = pathpath.parts[libname_index]
    elif PROJECT_NAME in pathpath.parts:
      libname_index = pathpath.parts.index(PROJECT_NAME)
      libname = pathpath.parts[libname_index]
    elif "src" in pathpath.parts:
      libname_index = pathpath.parts.index("src")
      libname = pathpath.parts[libname_index]
    elif "Lib" in pathpath.parts:
      libname_index = pathpath.parts.index("Lib") + 1
      libname = pathpath.parts[libname_index]
    else:
      libname_index = 0
      libname = PROJECT_NAME

    libpath = ".".join(pathpath.parts[libname_index:])

    length = len(libpath)

    if length > max_width:
      max_width = length
      with MAX_WIDTH_FILE.open("w") as f:
        f.write(str(max_width))

    self.libname = libname
    if "src." in libpath:
      libpath = libpath.split("src.", 1)[1]

    self.libpath = libpath

    super().__init__(*args, **kwargs)


class FixedFormatter(logging.Formatter):
  default_msec_format = None

  def formatTime(self, record, datefmt=None):
    """
    Return the creation time of the specified LogRecord as formatted text.

    This method should be called from format() by a formatter which
    wants to make use of a formatted time. This method can be overridden
    in formatters to provide for any specific requirement, but the
    basic behaviour is as follows: if datefmt (a string) is specified,
    it is used with time.strftime() to format the creation time of the
    record. Otherwise, an ISO8601-like (or RFC 3339-like) format is used.
    The resulting string is returned. This function uses a user-configurable
    function to convert the creation time to a tuple. By default,
    time.localtime() is used; to change this for a particular formatter
    instance, set the 'converter' attribute to a function with the same
    signature as time.localtime() or time.gmtime(). To change it for all
    formatters, for example if you want all logging times to be shown in GMT,
    set the 'converter' attribute in the Formatter class.
    """
    dt = datetime.fromtimestamp(record.created)
    if datefmt:
      s = dt.strftime(datefmt)
    else:
      s = dt.strftime(self.default_time_format)
      if self.default_msec_format:
        s = self.default_msec_format % (s, record.msecs)
    return s


class CustomTimedRotatingFileHandler(TimedRotatingFileHandler):
  def doRollover(self):
    """
    do a rollover; in this case, a date/time stamp is appended to the filename
    when the rollover happens.  However, you want the file to be named for the
    start of the interval, not the current time.  If there is a backup count,
    then we have to get a list of matching filenames, sort them and remove
    the one with the oldest suffix.
    """
    base_path = Path(self.baseFilename)
    # get the time that this sequence started at and make it a TimeTuple
    currentTime = int(time())
    t = self.rolloverAt - self.interval
    if self.utc:
      timeTuple = gmtime(t)
    else:
      timeTuple = localtime(t)
      dstNow = localtime(currentTime)[-1]
      dstThen = timeTuple[-1]
      if dstNow != dstThen:
        addend = 3600 if dstNow else -3600
        timeTuple = localtime(t + addend)
    dfn = base_path.with_name(self.rotation_filename(f"{base_path.stem}.{strftime(self.suffix, timeTuple)}{base_path.suffix}"))
    if dfn.exists():
      # Already rolled over.
      return

    if self.stream:
      self.stream.close()
      self.stream = None  # type: ignore
    self.rotate(self.baseFilename, str(dfn))
    if self.backupCount > 0:
      for s in self.getFilesToDelete():
        Path(s).unlink()
    if not self.delay:
      self.stream = self._open()
    self.rolloverAt = self.computeRollover(currentTime)


FILE_FORMATTER = FixedFormatter(
  fmt=f"{{libpath: <{max_width}}} | [{{asctime}}] | {{levelname: >8}} | {{message}}",
  datefmt=LOGGING_TIMESTAMP_FORMAT,
  style="{",
)


LOGGING_BASE_NAME = "timeclockentryprocessor"


DEBUG_LOG_LOC = LOG_LOC_FOLDER / f"{LOGGING_BASE_NAME}_debug.txt"
INFO_LOG_LOC = LOG_LOC_FOLDER / f"{LOGGING_BASE_NAME}.txt"

# SCHEDULER_LOG_LOC = LOG_LOC_FOLDER / "scheduler_logs"
# SCHEDULER_LOG_LOC.mkdir(exist_ok=True, parents=True)

# APSCHEDULER_DEBUG_LOG_LOC = SCHEDULER_LOG_LOC / "scheduler_debug.txt"
# APSCHEDULER_INFO_LOG_LOC = SCHEDULER_LOG_LOC / "scheduler.txt"


LOGGING_TYPE: Literal["daily", "per_run"] = "per_run"


if LOGGING_TYPE == "per_run":
  debug_file_handler = RotatingFileHandler(DEBUG_LOG_LOC, maxBytes=0, backupCount=30, delay=True)
  info_file_handler = RotatingFileHandler(INFO_LOG_LOC, maxBytes=0, backupCount=30, delay=True)
  debug_file_handler.doRollover()
  info_file_handler.doRollover()
else:
  debug_file_handler = CustomTimedRotatingFileHandler(DEBUG_LOG_LOC, when="midnight", backupCount=14, delay=True)
  nfo_handler = CustomTimedRotatingFileHandler(INFO_LOG_LOC, when="midnight", backupCount=14, delay=True)


def configure_logging():
  logging.setLogRecordFactory(FixedLogRecord)

  paramiko = logging.getLogger("paramiko")
  paramiko.setLevel(logging.WARNING)

  fonttools_subset = logging.getLogger("fontTools.subset")
  fonttools_subset.setLevel(logging.WARNING)

  # scheduler = logging.getLogger("apscheduler")
  # scheduler.propagate = False

  # scheduler_log_queue = Queue(-1)

  # scheduler_queue_handler = QueueHandler(scheduler_log_queue)

  # scheduler_info_handler = CustomTimedRotatingFileHandler(APSCHEDULER_INFO_LOG_LOC, when="midnight", backupCount=14, delay=True)
  # scheduler_debug_handler = CustomTimedRotatingFileHandler(APSCHEDULER_DEBUG_LOG_LOC, when="midnight", backupCount=14, delay=True)
  # scheduler_info_handler.setLevel(logging.INFO)
  # scheduler_debug_handler.setLevel(logging.DEBUG)

  # scheduler_queue_listener = QueueListener(
  #   scheduler_log_queue,
  #   scheduler_debug_handler,
  #   scheduler_info_handler,
  #   respect_handler_level=True,
  # )

  # scheduler.addHandler(scheduler_queue_handler)

  root = logging.getLogger()
  root.setLevel(logging.DEBUG if __debug__ else logging.INFO)
  # root.setLevel(logging.DEBUG)

  debug_file_handler.setLevel(logging.DEBUG)

  info_file_handler.setLevel(logging.INFO)

  console_info_handler = FixedRichHandler(
    # level=logging.DEBUG if __debug__ else logging.INFO,
    show_time=platform == "win32",
    console=RICH_CONSOLE,
    rich_tracebacks=True,
    log_time_format=LOGGING_TIMESTAMP_FORMAT,
    tracebacks_show_locals=True,
  )

  console_info_handler.setLevel(logging.INFO)

  debug_file_handler.setFormatter(FILE_FORMATTER)
  info_file_handler.setFormatter(FILE_FORMATTER)

  log_queue = Queue(-1)

  queue_handler = QueueHandler(log_queue)

  queue_listener = QueueListener(
    log_queue,
    debug_file_handler,
    info_file_handler,
    respect_handler_level=True,
  )

  root.addHandler(queue_handler)
  root.addHandler(console_info_handler)

  queue_listener.start()
  # scheduler_queue_listener.start()

  atexit.register(queue_listener.stop)
  # atexit.register(scheduler_queue_listener.stop)
