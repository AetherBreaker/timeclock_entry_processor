from __future__ import annotations

import atexit
import logging
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from sys import platform
from time import gmtime, localtime, strftime, time
from typing import TYPE_CHECKING

from environment_init_vars import SETTINGS
from rich.console import Console
from rich.logging import RichHandler
from rich.traceback import install

if TYPE_CHECKING:
  from multiprocessing import Queue as ProcessQueue
  from queue import Queue as ThreadQueue
  from typing import Literal

  from rich.console import ConsoleRenderable
  from rich.traceback import Traceback


PROJECT_NAME = "timeclockentryprocessor"
LOGGING_BASE_NAME = "timeclockentryprocessor"

LOGGING_TYPE: Literal["daily", "per_run"] = "per_run"

DEFAULT_MAX_WIDTH = 36

CWD = Path.cwd()

LOG_LOC_FOLDER = SETTINGS.persisted_dir_loc / "logs"
DEBUG_LOG_LOC = LOG_LOC_FOLDER / f"{LOGGING_BASE_NAME}_debug.txt"
INFO_LOG_LOC = LOG_LOC_FOLDER / f"{LOGGING_BASE_NAME}.txt"

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
    global DEFAULT_MAX_WIDTH
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

    if length > DEFAULT_MAX_WIDTH:
      DEFAULT_MAX_WIDTH = length
      with MAX_WIDTH_FILE.open("w") as f:
        f.write(str(DEFAULT_MAX_WIDTH))

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


ROOT = logging.getLogger()
ROOT.setLevel(logging.DEBUG if __debug__ else logging.INFO)


logging.setLogRecordFactory(FixedLogRecord)

paramiko = logging.getLogger("paramiko")
paramiko.setLevel(logging.WARNING)

fonttools_subset = logging.getLogger("fontTools.subset")
fonttools_subset.setLevel(logging.WARNING)


def configure_logging(rich_console: Console, mp: bool = False) -> tuple[ThreadQueue | ProcessQueue, ...]:
  from multiprocessing import parent_process

  if parent_process() is not None:
    raise RuntimeError("configure_logging should only be called from the main process")

  LOG_LOC_FOLDER.mkdir(exist_ok=True, parents=True)

  install(show_locals=True)

  if LOGGING_TYPE == "per_run":
    debug_file_handler = RotatingFileHandler(DEBUG_LOG_LOC, maxBytes=0, backupCount=30, delay=True)
    info_file_handler = RotatingFileHandler(INFO_LOG_LOC, maxBytes=0, backupCount=30, delay=True)
    debug_file_handler.doRollover()
    info_file_handler.doRollover()
  else:
    debug_file_handler = CustomTimedRotatingFileHandler(DEBUG_LOG_LOC, when="midnight", backupCount=14, delay=True)
    info_file_handler = CustomTimedRotatingFileHandler(INFO_LOG_LOC, when="midnight", backupCount=14, delay=True)

  debug_file_handler.setLevel(logging.DEBUG)
  info_file_handler.setLevel(logging.INFO)

  file_formatter = FixedFormatter(
    fmt=f"{{libpath: <{DEFAULT_MAX_WIDTH}}} | [{{asctime}}] | {{levelname: >8}} | {{message}}",
    datefmt=LOGGING_TIMESTAMP_FORMAT,
    style="{",
  )

  debug_file_handler.setFormatter(file_formatter)
  info_file_handler.setFormatter(file_formatter)

  console_info_handler = FixedRichHandler(
    # level=logging.DEBUG if __debug__ else logging.INFO,
    show_time=platform == "win32",
    console=rich_console,
    rich_tracebacks=True,
    log_time_format=LOGGING_TIMESTAMP_FORMAT,
    tracebacks_show_locals=True,
  )

  console_info_handler.setLevel(logging.INFO)

  if mp:
    from multiprocessing import Queue
  else:
    from queue import Queue

  file_log_queue = Queue(-1)
  console_log_queue = Queue(-1)

  queue_handler = QueueHandler(file_log_queue)
  console_queue_handler = QueueHandler(console_log_queue)

  handlers_for_queue: list[logging.Handler] = [
    debug_file_handler,
    info_file_handler,
  ]

  if mp:
    handlers_for_queue.append(console_info_handler)

  else:
    ROOT.addHandler(console_info_handler)

  file_queue_listener = QueueListener(
    file_log_queue,
    debug_file_handler,
    info_file_handler,
    respect_handler_level=True,
  )
  console_queue_listener = QueueListener(
    console_log_queue,
    console_info_handler,
    respect_handler_level=True,
  )

  ROOT.addHandler(queue_handler)
  ROOT.addHandler(console_queue_handler)

  file_queue_listener.start()
  console_queue_listener.start()

  atexit.register(file_queue_listener.stop)
  atexit.register(console_queue_listener.stop)

  return file_log_queue, console_log_queue


def configure_multiprocessing_logging(queues: tuple[ProcessQueue, ...]):
  from multiprocessing import current_process

  if current_process().name == "MainProcess":
    raise RuntimeError("configure_multiprocessing_logging should only be called from child processes")

  for queue in queues:
    queue_handler = QueueHandler(queue)
    ROOT.addHandler(queue_handler)
