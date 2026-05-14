from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console
from rich_custom import ProgressCustom


@contextmanager
def get_active_progress(console: Console) -> Iterator[ProgressCustom]:
  ephemeral = True
  # Progress instances are usually found inside the active Live instance
  if console._live_stack:
    active_live = console._live_stack[0]
    # Check if the renderable being displayed is a Progress instance
    if isinstance(active_live.renderable, (ProgressCustom)):
      progress = active_live.renderable
      ephemeral = False
  # If no active Progress instance is found, yield a new ProgressCustom instance
  progress = ProgressCustom(console=console)
  try:
    if ephemeral:
      progress.__enter__()
    yield progress
  except Exception as e:
    if ephemeral:
      progress.__exit__(type(e), e, e.__traceback__)
    else:
      raise e
  finally:
    if ephemeral:
      progress.__exit__(None, None, None)
