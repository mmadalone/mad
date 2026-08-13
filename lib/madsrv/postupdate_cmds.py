"""Post-SteamOS-update reapply, run from inside the MAD panel.

A SteamOS SYSTEM update keeps /home but RESETS the immutable root (systemd/pacman/etc), wiping the
persistence this rig needs; deck-post-update.sh re-applies it but needs sudo. esde-health-check.sh
detects an update at launch (BUILD_ID vs .last-os-build) and writes the pending flag. This module
lets the panel run the restore natively instead of nudging the user to Desktop Mode:

- postupdate.status          -> {pending, missing:[...], build}    (fast; flag + no-sudo --check)
- postupdate.run             -> STREAM: run deck-post-update.sh UNDER A PTY, feed the sudo password
                                to the prompt, emit {line} per output line, {auth_failed} on a bad
                                password (the page re-prompts with a fresh run, up to 3x), and
                                {done, rc, failed:[steps]} at the end.

There is NO "Later"/dismiss RPC (postupdate.clear_pending was retired audit 2026-08-12 phase 5: dead
RPC, no caller - the C++ page never sent it). This is DELIBERATE, not an oversight: the pending flag
is meant to keep reappearing until the user actually runs the reapply, and only deck-post-update.sh
itself clears it (on a successful run, see its rm -f of .post-update-pending). Do not add a dismiss
path here without confirming that design has changed.

SECURITY: the password is used ONLY to answer the PTY sudo prompt. It is never logged, never written
to disk, and never placed in an exception message; the only reference we hold is released right after
the feed (a Python str cannot be securely zeroed in-process, so the rebind is best-effort, not a
guaranteed wipe). PTY echo is disabled so sudo's tty cannot echo it back into the streamed output.
Running under a PTY
(not a bare pipe) is what lets the script's per-step `sudo` reuse the one warmed ticket - verified on
this Deck's sudoers.
"""
from __future__ import annotations

import os
import re
import select
import signal
import subprocess
import threading
from pathlib import Path

from .env_hygiene import clean_env
from .rpc import RpcError, Stream, method

LAUNCHERS = Path(__file__).resolve().parents[2]
SCRIPT = Path(os.environ.get("DECK_POSTUPDATE_SCRIPT", str(LAUNCHERS / "deck-post-update.sh")))
# Runtime flag written by esde-health-check.sh when an OS update wiped something (overridable=tests).
PENDING = Path(os.environ.get("MAD_POSTUPDATE_FLAG", str(LAUNCHERS / ".post-update-pending")))

_RUN_ACTIVE = threading.Lock()           # one reapply at a time
_AUTH_FAIL_RE = re.compile(r"try again|incorrect password|authentication fail", re.I)
_FAILED_RE = re.compile(r"Some steps FAILED:\s*(.*)")
# Strip ANSI / terminal control noise (pacman's colour codes + progress-bar cursor moves) so the
# streamed log renders as readable text, not "[?25l[1;34m..." garbage, in the panel's plain renderer.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?=]*[A-Za-z]|\x1b[()#][0-9A-Za-z]|[\x00-\x08\x0b-\x1f\x7f]")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


# Force a UNIQUE sudo prompt so the PTY reader matches ONLY the real sudo prompt, never a sub-script
# that merely prints "password for" / "[sudo]" (e.g. samba-setup.sh's "Samba password for 'deck'"),
# which previously looked like a SECOND sudo prompt and false-tripped "wrong password too many times".
_SUDO_PROMPT = "[mad-reapply-sudo] password:"


def _os_build() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("BUILD_ID="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def _check_lines() -> list:
    """Raw --check output lines (empty on any failure)."""
    try:
        p = subprocess.run([str(SCRIPT), "--check"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]


def _split_skew(lines: list) -> tuple:
    """(components a SteamOS update wiped, script-skew lines) - two different problems.

    --check reports both, but they must NOT be merged here. The page computes
    needed = pending or missing, then states that a SteamOS update reset system files
    and offers a sudo REAPPLY. Skew is not that: nothing was wiped, and the reapply
    runs deck-post-update.sh, which does not git-pull and so can never clear it. Merged,
    a skewed Deck gets a false claim plus an offer that is guaranteed not to work -
    the same trap esde-health-check.sh already filters this line out of.
    """
    from lib.version_skew import SKEW_PREFIX
    missing = [ln for ln in lines if not ln.startswith(SKEW_PREFIX)]
    skew = [ln for ln in lines if ln.startswith(SKEW_PREFIX)]
    return missing, skew


def _missing() -> list:
    """The no-sudo health check: which components a SteamOS update wiped (empty = all present)."""
    return _split_skew(_check_lines())[0]


class PostUpdateStream(Stream):
    """Runs deck-post-update.sh under a PTY, feeding the sudo password to the prompt and streaming
    its output. See the module docstring for the password-safety contract."""

    def __init__(self, argv: list, password: str):
        super().__init__()
        self._argv = argv
        self._pw = password           # dropped right after the single feed
        self._pid = None
        threading.Thread(target=self._stop_watcher, daemon=True,
                         name=f"{self.token}-stopwatch").start()

    def _stop_watcher(self):
        self.stopped.wait()
        self._kill()

    def _kill(self):
        pid = self._pid
        if pid is None:
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass

    def cleanup(self):
        self._kill()

    def run(self):
        import pty
        import termios
        pw = self._pw
        self._pw = None               # no lingering attribute reference
        # Build the child env in the PARENT (before fork): forkpty in a multi-threaded process can
        # deadlock if the child touches a lock held at fork time, so the child must do as little as
        # possible before exec. LC_ALL=C -> locale-independent sudo/PAM messages (a Spanish session
        # would otherwise print "Lo siento, intentelo de nuevo", which the auth-fail regex misses).
        child_env = clean_env()       # strip the Steam overlay from LD_PRELOAD (see env_hygiene:
                                      # its ld.so flood breaks the PTY sudo password handshake)
        child_env["LC_ALL"] = "C"
        child_env["LANGUAGE"] = ""
        child_env["SUDO_PROMPT"] = _SUDO_PROMPT              # unambiguous prompt for the detector below
        child_env["MAD_POSTUPDATE_NONINTERACTIVE"] = "1"     # sub-scripts must not block on their own prompts
        argv = list(self._argv)
        pid, fd = pty.fork()
        if pid == 0:                  # child: ONLY exec (no allocation) between fork and exec
            try:
                os.execvpe(argv[0], argv, child_env)
            except Exception:
                os._exit(127)
        self._pid = pid
        # Disable echo so the fed password is never echoed back into the stream.
        try:
            attr = termios.tcgetattr(fd)
            attr[3] &= ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSANOW, attr)
        except Exception:
            pass

        fed = False
        auth_failed = False
        failed: list = []
        probe = b""                   # small rolling buffer to spot the (newline-less) sudo prompt
        carry = ""                    # incomplete trailing line
        try:
            while not self.stopped.is_set():
                try:
                    r, _, _ = select.select([fd], [], [], 0.5)
                except OSError:
                    break
                if not r:
                    continue
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                probe = (probe + data)[-256:]
                if _SUDO_PROMPT.encode() in probe:
                    if not fed:
                        if pw:
                            try:
                                os.write(fd, (pw + "\n").encode())
                            except OSError:
                                pass
                            pw = "x" * len(pw)   # best-effort drop (a Python str cannot be zeroed)
                            pw = None
                        else:
                            auth_failed = True   # sudo prompts but we have no password (passwordless expected)
                        fed = True
                        probe = b""              # forget this prompt so a RE-prompt is detectable
                    else:
                        # A SECOND prompt after we already answered = the password was rejected.
                        # Locale-INDEPENDENT: does not rely on sudo's English "try again" text, and
                        # stops us hanging (we have no second password to feed).
                        auth_failed = True
                    if auth_failed:
                        break
                carry += data.decode("utf-8", "replace")
                lines = carry.split("\n")
                carry = lines.pop()          # keep the incomplete tail
                for ln in lines:
                    ln = _strip_ansi(ln.rstrip("\r"))
                    if _AUTH_FAIL_RE.search(ln):
                        auth_failed = True
                    m = _FAILED_RE.search(ln)
                    if m:
                        # Real line: "...FAILED:<steps> - re-run this script." (an em-dash in the
                        # actual script). Keep only the step tokens before that trailing clause.
                        failed = re.split(r"\s[-—]\s", m.group(1))[0].split()
                    if ln.strip():
                        self.emit({"line": ln})
                if auth_failed:
                    break                    # abort; the page re-prompts with a fresh run
        finally:
            pw = None                        # belt-and-braces scrub
            self._kill()
            rc = self._reap()
            try:
                os.close(fd)                 # release the PTY master fd
            except OSError:
                pass
            if self.stopped.is_set():
                return                       # cancelled: no terminal event (mirrors the backup stream)
            if auth_failed:
                self.emit({"auth_failed": True})
            else:
                self.emit({"done": True, "rc": rc, "failed": failed})

    def _reap(self) -> int:
        pid = self._pid
        if pid is None:
            return -1
        self._pid = None            # reaped: a later cleanup()/_kill() must not SIGTERM a reused PID
        try:
            _, status = os.waitpid(pid, 0)
        except OSError:
            return -1
        return os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1


def _sudo_passwordless() -> bool:
    """True if sudo needs NO password here (a NOPASSWD grant is active). `-n` never prompts."""
    try:
        return subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@method("postupdate.status")
def _postupdate_status(params):
    # One --check run, split into the two problems it can report. `scripts_skew` is a
    # SEPARATE field on purpose: it must never feed the page's needed/REAPPLY logic (see
    # _split_skew). An older panel simply ignores the extra key.
    missing, skew = _split_skew(_check_lines())
    return {"pending": PENDING.exists(), "missing": missing, "scripts_skew": skew,
            "build": _os_build(), "sudo_passwordless": _sudo_passwordless()}


@method("postupdate.run")
def _postupdate_run(params):
    # NOTE: never place the password in an error message / log (rpc._run prints tracebacks).
    pw = (params or {}).get("password") or ""
    if not isinstance(pw, str):
        raise RpcError("EINVAL", "invalid password")
    # A password is required UNLESS passwordless sudo is active (then run with no prompt).
    if not pw and not _sudo_passwordless():
        raise RpcError("EINVAL", "a sudo password is required (or enable passwordless sudo)")
    if not SCRIPT.exists():
        raise RpcError("ENOENT", "deck-post-update.sh not found")
    if not _RUN_ACTIVE.acquire(blocking=False):
        raise RpcError("EBUSY", "a post-update reapply is already running")
    # Construct + start INSIDE the try so a rare failure (e.g. can't-start-thread) releases the lock
    # rather than wedging every future run at EBUSY.
    try:
        stream = PostUpdateStream([str(SCRIPT)], pw)
        _orig_cleanup = stream.cleanup   # release the single-run lock when the stream ends

        def _cleanup_and_release():
            try:
                _orig_cleanup()
            finally:
                if _RUN_ACTIVE.locked():
                    try:
                        _RUN_ACTIVE.release()
                    except RuntimeError:
                        pass
        stream.cleanup = _cleanup_and_release
        return {"stream": stream.start()}
    except Exception:
        if _RUN_ACTIVE.locked():
            try:
                _RUN_ACTIVE.release()
            except RuntimeError:
                pass
        raise
