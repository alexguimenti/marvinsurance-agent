"""Tell the app, every minute, that work is still going. On Runtime a session can end, and a laptop's agent can restart: work
the app has not heard about for five minutes counts as stopped."""
import logging
import threading
from contextlib import contextmanager
from typing import Callable, Iterator

log = logging.getLogger(__name__)
EVERY_S = 60


@contextmanager
def heartbeat(send: Callable[[], None], every_s: float = EVERY_S) -> Iterator[None]:
    """Call `send` every `every_s` seconds, on a thread of its own, while the block runs. A failed call is logged and
    ignored: the next one tries again."""
    done = threading.Event()

    def beat() -> None:
        while not done.wait(every_s):
            try:
                send()
            except Exception as exc:
                log.warning("could not tell the app the work is going: %s", exc)

    thread = threading.Thread(target=beat, name="heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join(5)
