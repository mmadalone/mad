"""In-ES-DE post-SteamOS-update reapply (postupdate_cmds).

Drives postupdate_cmds directly with a STUB deck-post-update.sh + a STUB `sudo` on PATH, exercising
the REAL PTY plumbing (pty.fork) - the password feed, auth-fail detection, FAILED-step parse, and the
security contract that the password never appears in the streamed events. No real sudo/root needed,
so these RUN on CI.

Run:  python3 -m unittest tests.test_postupdate -v
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.madsrv import postupdate_cmds as pu   # noqa: E402
from lib.madsrv import rpc                       # noqa: E402

GOOD_PW = "hunter2"

_STUB_SUDO = """#!/usr/bin/env bash
GOOD="%s"; TICK="${STUB_SUDO_TICKET:?}"
_auth(){ local t=0 pw
  while [ $t -lt 3 ]; do
    printf '[sudo] password for %%s: ' "$USER" >&2
    IFS= read -r pw || return 1
    if [ "$pw" = "$GOOD" ]; then : >"$TICK"; return 0; fi
    printf '\\nSorry, try again.\\n' >&2; t=$((t+1))
  done
  printf '\\nsudo: 3 incorrect password attempts\\n' >&2; return 1; }
case "$1" in
  -v) [ -f "$TICK" ] || _auth; exit $?;;
  -n) shift; [ -f "$TICK" ] && exec "$@"; exit 1;;
  *)  [ -f "$TICK" ] || _auth || exit 1; exec "$@";;
esac
""" % GOOD_PW

# Stub deck-post-update.sh: --check prints STUB_MISSING lines; a normal run does sudo -v + a per-step
# sudo, optionally emits the real "Some steps FAILED:" line (STUB_FAIL), then the done line.
_STUB_SCRIPT = """#!/usr/bin/env bash
set -uo pipefail
if [ "${1:-}" = "--check" ]; then printf '%s\\n' "${STUB_MISSING:-}"; exit 0; fi
echo "[post-update] 1/3 warming sudo"
sudo -v || { echo "[post-update] auth failed"; exit 1; }
echo "[post-update] 2/3 root step"; sudo -n true && echo "[post-update]   root OK"
[ -n "${STUB_FAIL:-}" ] && echo "!! Some steps FAILED:${STUB_FAIL} - re-run this script."
echo "[post-update] 3/3 done"
"""


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bindir = self.tmp / "bin"; self.bindir.mkdir()
        (self.bindir / "sudo").write_text(_STUB_SUDO); (self.bindir / "sudo").chmod(0o755)
        self.script = self.tmp / "deck-post-update.sh"
        self.script.write_text(_STUB_SCRIPT); self.script.chmod(0o755)
        self.flag = self.tmp / ".post-update-pending"
        self.tick = self.tmp / ".tick"
        # patch module globals + the runtime env the PTY child inherits
        self._save = (pu.SCRIPT, pu.PENDING, os.environ.get("PATH"),
                      os.environ.get("STUB_SUDO_TICKET"), os.environ.get("STUB_MISSING"),
                      os.environ.get("STUB_FAIL"))
        pu.SCRIPT = self.script
        pu.PENDING = self.flag
        os.environ["PATH"] = str(self.bindir) + ":" + os.environ["PATH"]
        os.environ["STUB_SUDO_TICKET"] = str(self.tick)

    def tearDown(self):
        pu.SCRIPT, pu.PENDING, path, tick, miss, fail = self._save
        for k, v in (("PATH", path), ("STUB_SUDO_TICKET", tick),
                     ("STUB_MISSING", miss), ("STUB_FAIL", fail)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if pu._RUN_ACTIVE.locked():
            try:
                pu._RUN_ACTIVE.release()
            except RuntimeError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_collect(self, password, timeout=15):
        """Start postupdate.run, capture the protocol events for its token, wait for completion."""
        cap = io.StringIO()
        with contextlib.redirect_stdout(cap):
            resp = pu._postupdate_run({"password": password})
            token = resp["stream"]
            end = time.time() + timeout
            while time.time() < end and token in rpc._STREAMS:
                time.sleep(0.05)
        events = []
        for line in cap.getvalue().splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("event") == "stream" and obj.get("stream") == token:
                events.append(obj["data"])
        return events


class PostUpdateStatusFlag(_Base):
    def test_status_pending_and_missing(self):
        os.environ["STUB_MISSING"] = "Samba file sharing"
        self.flag.write_text("Samba file sharing\n")
        st = pu._postupdate_status({})
        self.assertTrue(st["pending"], "flag present -> pending")
        self.assertIn("Samba file sharing", st["missing"])

    def test_status_not_pending_when_no_flag(self):
        self.assertFalse(pu._postupdate_status({})["pending"])

    def test_clear_pending_removes_flag(self):
        self.flag.write_text("x")
        pu._postupdate_clear({})
        self.assertFalse(self.flag.exists())
        pu._postupdate_clear({})   # idempotent


class PostUpdateRun(_Base):
    def test_correct_password_streams_and_done(self):
        ev = self._run_collect(GOOD_PW)
        lines = [e["line"] for e in ev if "line" in e]
        self.assertTrue(any("root OK" in l for l in lines),
                        f"per-step sudo must reuse the warmed ticket; got {lines}")
        done = [e for e in ev if e.get("done")]
        self.assertEqual(len(done), 1, ev)
        self.assertEqual(done[0]["failed"], [], "no failed steps on a clean run")

    def test_wrong_password_reports_auth_failed(self):
        ev = self._run_collect("wrong-pw")
        self.assertTrue(any(e.get("auth_failed") for e in ev), ev)
        self.assertFalse(any(e.get("done") for e in ev), "auth fail must not report done")

    def test_failed_steps_are_parsed(self):
        os.environ["STUB_FAIL"] = " samba sinden-deps"
        ev = self._run_collect(GOOD_PW)
        done = [e for e in ev if e.get("done")]
        self.assertTrue(done and done[0]["failed"] == ["samba", "sinden-deps"], done)

    def test_password_never_appears_in_stream(self):
        secret = "S3cr3t-Pw-hunter2"   # contains the good pw as a substring; must still not leak
        os.environ["STUB_SUDO_TICKET"] = str(self.tick)
        # use a sudo stub that accepts this exact secret
        (self.bindir / "sudo").write_text(_STUB_SUDO.replace(GOOD_PW, secret))
        (self.bindir / "sudo").chmod(0o755)
        ev = self._run_collect(secret)
        blob = json.dumps(ev)
        self.assertNotIn(secret, blob, "the password must never appear in any streamed event")

    def test_second_run_is_rejected_while_active(self):
        # hold the run lock -> a concurrent run must EBUSY, not start a second PTY.
        self.assertTrue(pu._RUN_ACTIVE.acquire(blocking=False))
        try:
            from lib.madsrv.rpc import RpcError
            with self.assertRaises(RpcError):
                pu._postupdate_run({"password": GOOD_PW})
        finally:
            pu._RUN_ACTIVE.release()

    def test_missing_password_rejected(self):
        from lib.madsrv.rpc import RpcError
        orig = pu._sudo_passwordless
        pu._sudo_passwordless = lambda: False   # deterministic: no passwordless -> a password is required
        try:
            with self.assertRaises(RpcError):
                pu._postupdate_run({})
        finally:
            pu._sudo_passwordless = orig

    def test_passwordless_runs_without_a_password(self):
        # passwordless sudo active -> no password required; the run streams to completion, no prompt.
        Path(self.tick).write_text("")          # ticket present -> stub `sudo -v` succeeds w/o a prompt
        orig = pu._sudo_passwordless
        pu._sudo_passwordless = lambda: True
        try:
            ev = self._run_collect("")          # no password
        finally:
            pu._sudo_passwordless = orig
        self.assertTrue([e for e in ev if e.get("done")], f"passwordless run should complete: {ev}")
        self.assertFalse([e for e in ev if e.get("auth_failed")], "no auth failure when passwordless")

    def test_status_reports_passwordless_flag(self):
        st = pu._postupdate_status({})
        self.assertIn("sudo_passwordless", st)
        self.assertIsInstance(st["sudo_passwordless"], bool)


if __name__ == "__main__":
    unittest.main()
