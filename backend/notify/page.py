"""The in-band FAILURE PAGE (H3, 2026-10-16) — ``python -m notify.page --asof YYYY-MM-DD --reason "..."``.

R4's ``HealthEvent`` pages from INSIDE a pass that finished, so a pass that died, hung, or never reached the
database had no voice at all. MEASURED: the 10-12 run killed mid-way at 22:42 (a WSL auto-update took the VM
down) and the 10-01 boot catch-up lost to the cold-start DB race both paged NOTHING; the operator found out
by hand. When a pass fails but the box stays up (a crash, an OOM kill, a DB that drops mid-run, a hang the
shell's timeout stops, a boot that cannot reach the DB as on 10-01), the cron sidecar's SHELL
(``scripts/daily_cron.sh``) is still standing, so it calls this CLI once the night has failed for good: the
scheduled run's ONE retry failed, the boot catch-up's retry failed, a catch-up night failed, or a backup
failed. A box that goes down entirely (10-12: the VM itself was killed) can page nothing from inside; that
is the external heartbeat's job (H2, the same script).

Three properties, each load-bearing:

- **It needs no database.** The fault being paged is often the database itself (refused, starting up, gone),
  so this module imports nothing from ``db`` / ``repositories`` / ``pipeline`` and opens no connection. A
  test pins it: importing ``notify.page`` in a fresh interpreter loads neither ``db`` nor ``psycopg``.
- **It never raises, and it always exits 0.** The shell treats it as fire-and-forget on a path that must
  never block the loop; a page that crashed would just be a second silent failure. Delivery faults are
  LOGGED (the Slack adapter's own fail-open warning, or this module's fallback lines), never propagated.
- **The message carries no thesis names.** The shell composes the reason from the as-of, the failure shape
  and an exit code; nothing here reads a thesis, so nothing here can leak one.

Delivery rides the EXISTING seam, so the channel choice stays in one place: a ``RunFailurePage`` (a
``HealthEvent`` whose label is the reason) goes through ``get_notifier().notify_health`` — Slack when
``SLACK_WEBHOOK_URL`` is set, else the LogNotifier's loud ``CRON HEALTH`` line. If that seam cannot even be
built (``get_settings()`` raising on a malformed env value, the same fault that would also have failed the
pass), a last-resort stdlib POST to the raw ``SLACK_WEBHOOK_URL`` keeps the page from dying with it.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.request
from dataclasses import dataclass
from datetime import date

from notify import HealthEvent, Notifier, get_notifier

_log = logging.getLogger("alphadeck.notify.page")

# A page is one line in Slack and in the log: collapse whitespace and cap the length so a runaway reason
# (a pasted traceback) cannot turn the page into a wall.
_MAX_REASON = 400
_DEFAULT_REASON = "unspecified failure"
# the last-resort POST's wall bound; the shell also wraps the whole CLI in `timeout`
_FALLBACK_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class RunFailurePage(HealthEvent):
    """A run-level health page whose news is ONE failure the shell observed, not a pass's counts.

    A ``HealthEvent`` subclass so it rides ``Notifier.notify_health`` unchanged on every adapter (the
    LogNotifier records ``label`` loud; the SlackNotifier pushes ``🚨 {label}``). The inherited counts are
    zero by construction: no pass reported them, and ``label`` deliberately ignores them. ``asof`` may be
    ``None`` when the shell's date did not parse; the page still goes out.
    """

    reason: str = _DEFAULT_REASON

    @property
    def label(self) -> str:
        night = self.asof.isoformat() if self.asof else "(asof unknown)"
        return f"cron {night}: FAILED — {self.reason}"


def _clean(reason: str | None) -> str:
    text = " ".join((reason or "").split())
    if len(text) > _MAX_REASON:
        text = text[: _MAX_REASON - 1] + "…"
    return text or _DEFAULT_REASON


def _parse_asof(raw: str | None) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def failure_event(asof: date | None, reason: str | None) -> RunFailurePage:
    """The page for one failure, zero counts (see ``RunFailurePage``)."""
    return RunFailurePage(
        asof=asof,  # None is allowed here (the field is typed ``date``); ``label`` handles it
        theses=0,
        withheld_no_live=0,
        withheld_failure=0,
        errored=0,
        edgar_fetches=0,
        frozen=False,
        reason=_clean(reason),
    )


def _fallback_post(text: str) -> None:
    """The last resort when the notify seam itself cannot be built: a stdlib JSON POST to the raw
    ``SLACK_WEBHOOK_URL``. Never raises; never logs the URL (an incoming-webhook URL is a credential).
    """
    url = (os.environ.get("SLACK_WEBHOOK_URL") or "").strip()
    if not url:
        _log.error("failure page: no SLACK_WEBHOOK_URL, so this log line is the only record")
        return
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_FALLBACK_TIMEOUT_S):  # the operator-set URL
            pass
        _log.warning("failure page: delivered by the stdlib fallback")
    except Exception as exc:  # noqa: BLE001 — best-effort delivery, never raises
        _log.error("failure page: the stdlib fallback could not deliver (%s)", type(exc).__name__)


def send_page(asof: date | None, reason: str | None, *, notifier: Notifier | None = None) -> bool:
    """Deliver ONE failure page. Returns True when the notify seam took it (its adapter then owns delivery
    and logs its own failure), False when the seam could not be built or raised and the stdlib fallback ran
    instead. NEVER raises.

    ``notifier`` is the test seam (a fake that records the event); the CLI passes none and gets the
    configured ``get_notifier()``, resolved here so a broken Settings lands in the fallback, not a raise.
    """
    event = failure_event(asof, reason)
    try:
        sink = notifier if notifier is not None else get_notifier()
        sink.notify_health(event)
        return True
    except Exception:  # noqa: BLE001 — the page must survive a broken seam
        _log.exception("failure page: the notify seam failed; falling back to a raw webhook POST")
        # the record, in the same shape the LogNotifier writes
        _log.error("CRON HEALTH %s", event.label)
        _fallback_post(f"🚨 {event.label}")
        return False


class _Parser(argparse.ArgumentParser):
    """argparse that RAISES on a usage error instead of exiting 2: a mistyped shell call must still page."""

    def error(self, message: str):
        raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    """The CLI. Always returns 0 (``--help`` included); a usage error still sends a page, with a reason
    saying the call itself was malformed, because the shell only calls this when something already failed.
    """
    logging.basicConfig(level=logging.INFO, format="notify.page %(levelname)s: %(message)s")
    p = _Parser(
        prog="python -m notify.page",
        description="Send ONE failure page (Slack when SLACK_WEBHOOK_URL is set, else a loud log line). "
        "Needs no database; never raises; always exits 0.",
    )
    p.add_argument("--asof", default=None, help="the night that failed, YYYY-MM-DD")
    p.add_argument(
        "--reason",
        default=None,
        help="what failed, in a few words (no thesis names; the shell composes it)",
    )
    asof: date | None = None
    reason: str | None = None
    try:
        args = p.parse_args(argv)
        asof, reason = _parse_asof(args.asof), args.reason
    except SystemExit:  # --help / --version print and exit; nothing failed, so nothing to page
        return 0
    except Exception:  # noqa: BLE001 — a malformed call must still page
        reason = "the failure page was called with unparseable arguments"
    try:
        send_page(asof, reason)
    except (
        Exception
    ):  # noqa: BLE001 — send_page never raises; belt-and-suspenders for an exit-0 contract
        _log.exception("failure page: unexpected error")
    return 0


if __name__ == "__main__":
    sys.exit(main())
