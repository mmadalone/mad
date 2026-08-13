"""rpc._run must log WHY a structured error happened, without putting it on the wire.

Audit 2026-08-12 phase 5, the B904 policy decision. ruff's B904 flags 77 sites that do
`raise RpcError(...)` inside an `except` block without `from exc`. Adding `from exc` everywhere
would attach the original traceback to the exception the panel renders, which is the wrong place
for it: the user sees a dialog, not a stack. So the pattern stays and the finding is frozen in
tools/lint-baseline.txt, on the condition that the diagnosis is not lost. Python links the original
exception as __context__ implicitly, so rpc._run recovers it and writes it to mad-backend.log.

These tests are what makes that a contract instead of a comment.

Run:  python3 -m unittest tests.test_rpc_error_cause -v
"""
from __future__ import annotations

import contextlib
import io
import unittest

from lib.madsrv import rpc


class _Capture(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._real_send = rpc.send
        rpc.send = self.sent.append
        self.addCleanup(lambda: setattr(rpc, "send", self._real_send))

    def _run(self, fn):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rpc._run(1, "demo.method", fn, {}, ())
        return err.getvalue(), self.sent[-1]


class CauseChain(_Capture):
    def test_the_underlying_error_reaches_the_log(self):
        def fn(_params):
            try:
                {}["missing"]
            except KeyError:
                raise rpc.RpcError("EINVAL", "no such key")
        log, _wire = self._run(fn)
        self.assertIn("rpc demo.method -> EINVAL: no such key", log)
        self.assertIn("caused by KeyError: 'missing'", log)

    def test_the_cause_never_reaches_the_wire(self):
        # The panel renders the wire message at the user. A KeyError, a traceback or a filesystem
        # path leaking into a dialog is exactly what `from exc` at 77 sites would have caused.
        def fn(_params):
            try:
                open("/definitely/not/here/secret.ini")
            except OSError:
                raise rpc.RpcError("EIO", "could not read the config")
        _log, wire = self._run(fn)
        self.assertEqual(wire["error"], {"code": "EIO", "message": "could not read the config"})
        self.assertNotIn("secret.ini", str(wire))

    def test_explicit_from_exc_is_reported_too(self):
        # __cause__ (explicit `from`) is preferred over __context__ (implicit), so a site that DOES
        # use `from exc` is not silently dropped.
        def fn(_params):
            try:
                raise ValueError("root cause")
            except ValueError as exc:
                raise rpc.RpcError("EINVAL", "bad input") from exc
        log, _wire = self._run(fn)
        self.assertIn("caused by ValueError: root cause", log)

    def test_a_raise_with_no_cause_logs_the_plain_line(self):
        def fn(_params):
            raise rpc.RpcError("EINVAL", "just invalid")
        log, _wire = self._run(fn)
        self.assertIn("rpc demo.method -> EINVAL: just invalid", log)
        self.assertNotIn("caused by", log)

    def test_the_chain_is_bounded(self):
        # An unbounded __context__ walk can be long; the log line must stay one line.
        def fn(_params):
            try:
                try:
                    try:
                        try:
                            raise ValueError("deepest")
                        except ValueError:
                            raise TypeError("third")
                    except TypeError:
                        raise KeyError("second")
                except KeyError:
                    raise IndexError("first")
            except IndexError:
                raise rpc.RpcError("EINTERNAL", "gave up")
        log, _wire = self._run(fn)
        self.assertEqual(log.count("<-"), 2, "at most 3 links, so at most 2 separators")
        self.assertNotIn("deepest", log)
        self.assertEqual(len([ln for ln in log.splitlines() if ln.strip()]), 1)


if __name__ == "__main__":
    unittest.main()
