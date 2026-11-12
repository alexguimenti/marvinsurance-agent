"""The agent tells the app, every minute, that it is still working."""
import threading
import time

from agent.heartbeat import heartbeat


def test_the_heartbeat_reports_every_interval_and_stops_with_the_block():
    calls = []
    with heartbeat(lambda: calls.append(time.monotonic()), every_s=0.01):
        time.sleep(0.2)
    seen = len(calls)
    assert seen >= 3
    time.sleep(0.1)
    assert len(calls) == seen  # none once the block has exited


def test_a_failed_report_is_logged_and_the_next_one_goes_out(caplog):
    attempts = []

    def send():
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("the app did not answer")

    with heartbeat(send, every_s=0.01):
        time.sleep(0.2)
    assert len(attempts) >= 3 and any("could not tell the app" in r.message for r in caplog.records)


def test_the_blocks_own_error_is_not_swallowed():
    stopped = threading.Event()
    try:
        with heartbeat(stopped.set, every_s=0.01):
            raise ValueError("the work failed")
    except ValueError as exc:
        assert str(exc) == "the work failed"
    else:
        raise AssertionError("the error was swallowed")
