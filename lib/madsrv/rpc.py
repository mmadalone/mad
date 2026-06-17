"""NDJSON-over-stdio RPC core for mad-backend.py.

Wire format (one JSON object per line, UTF-8):
  request   {"id": N, "method": "...", "params": {...}}
  response  {"id": N, "ok": true,  "result": {...}}
            {"id": N, "ok": false, "error": {"code": "...", "message": "..."}}
  event     {"event": "...", "data": {...}}            (server push, no id)
  stream    {"event": "stream", "stream": "<tok>", "data": {...}}

Fast methods run inline on the stdin thread; @method(..., slow=True) runs on a
small worker pool (device probes, SDL init, file sweeps) so a slow call never
blocks the next request. All writes go through send() under one lock.
"""
from __future__ import annotations

import json
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from .. import staterev

_OUT_LOCK = threading.Lock()
_METHODS: dict = {}          # name -> (fn(params) -> dict, slow: bool, cache_deps: tuple)
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="madsrv")

# Revision-invalidated response cache (see lib/staterev.py): a method declaring
# cache=(dep keys) reuses its previous result until one of those revisions
# advances. Keyed by (method, params); the result is stamped with the rev
# snapshot taken BEFORE the handler ran, so a write that lands while a slow
# handler computes invalidates the entry on the next call (never serves stale).
_CACHE: dict = {}            # cache_key -> (snapshot: dict, result)
_CACHE_LOCK = threading.Lock()

_STREAMS: dict = {}          # token -> Stream
_STREAMS_LOCK = threading.Lock()
_TOK = [0]


def send(obj: dict) -> None:
    """Serialize + write one NDJSON line. Never raises into callers — a closed
    stdout means the panel is gone; the main loop notices EOF and exits."""
    try:
        line = json.dumps(obj, ensure_ascii=False, default=str)
        with _OUT_LOCK:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
    except Exception:
        pass


def event(name: str, data: dict) -> None:
    send({"event": name, "data": data})


def stream_event(token: str, data: dict) -> None:
    send({"event": "stream", "stream": token, "data": data})


def method(name: str, slow: bool = False, cache=()):
    """Register an RPC method. cache=(dep keys) opts the method into the
    revision-invalidated response cache (the result is reused until one of the
    named lib.staterev revisions advances); a truthy params["force"] always
    bypasses the cache and refreshes the entry."""
    def deco(fn):
        _METHODS[name] = (fn, slow, tuple(cache))
        return fn
    return deco


def _cache_key(name: str, params: dict) -> str:
    """Stable key for (method, params), ignoring the cache-control "force" flag
    so a forced refresh and a normal call share the same slot."""
    p = {k: v for k, v in (params or {}).items() if k != "force"}
    try:
        body = json.dumps(p, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        body = repr(sorted(p.items()))
    return name + "\x00" + body


def _cached_call(name, fn, params, deps):
    """Run fn(params) through the response cache when it declares deps."""
    if not deps:
        return fn(params)
    key = _cache_key(name, params)
    if not params.get("force"):
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
        if hit is not None and hit[0] == staterev.snapshot(deps):
            return hit[1]
    snap = staterev.snapshot(deps)      # BEFORE the handler reads state
    result = fn(params)
    with _CACHE_LOCK:
        _CACHE[key] = (snap, result)
    return result


def _run(req_id, name, fn, params, deps):
    try:
        result = _cached_call(name, fn, params or {}, deps)
        send({"id": req_id, "ok": True, "result": result if result is not None else {}})
    except RpcError as e:
        # Structured (EINVAL/precondition/…) errors went only on the wire → invisible in
        # mad-backend.log. Record method+code so a validation failure is diagnosable.
        print(f"rpc {name} -> {e.code}: {e}", file=sys.stderr)
        send({"id": req_id, "ok": False, "error": {"code": e.code, "message": str(e)}})
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        send({"id": req_id, "ok": False,
              "error": {"code": "EINTERNAL", "message": f"{type(e).__name__}: {e}"}})


def dispatch(req: dict) -> None:
    req_id = req.get("id")
    name = req.get("method")
    if not isinstance(req_id, int) or not isinstance(name, str):
        event("protocol_error", {"message": f"bad request shape: {req!r}"[:300]})
        return
    ent = _METHODS.get(name)
    if ent is None:
        send({"id": req_id, "ok": False,
              "error": {"code": "ENOMETHOD", "message": f"unknown method {name!r}"}})
        return
    fn, slow, deps = ent
    if slow:
        _POOL.submit(_run, req_id, name, fn, req.get("params"), deps)
    else:
        _run(req_id, name, fn, req.get("params"), deps)


class RpcError(Exception):
    """Raise inside a method for a structured error response."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Stream:
    """A live data source pushing stream_event(token, ...) from its own thread.
    Subclasses implement run() (loop until self.stopped is set) and may override
    cleanup() — cleanup MUST release any grabbed device / child process, and is
    guaranteed to run on stop_stream()/stop_all() (incl. daemon teardown)."""

    def __init__(self):
        with _STREAMS_LOCK:
            _TOK[0] += 1
            self.token = f"s{_TOK[0]}"
            _STREAMS[self.token] = self
        self.stopped = threading.Event()
        self._thread = threading.Thread(target=self._guarded, daemon=True,
                                        name=f"stream-{self.token}")

    def start(self) -> str:
        self._thread.start()
        return self.token

    def _guarded(self):
        try:
            self.run()
        except Exception:
            traceback.print_exc(file=sys.stderr)
        finally:
            try:
                self.cleanup()
            except Exception:
                traceback.print_exc(file=sys.stderr)
            with _STREAMS_LOCK:
                _STREAMS.pop(self.token, None)
            stream_event(self.token, {"closed": True})

    def run(self):                      # pragma: no cover - abstract
        raise NotImplementedError

    def cleanup(self):
        pass

    def emit(self, data: dict):
        stream_event(self.token, data)


def stop_stream(token: str) -> bool:
    with _STREAMS_LOCK:
        s = _STREAMS.get(token)
    if s is None:
        return False
    s.stopped.set()
    return True


def stop_all_streams(join_timeout: float = 2.0) -> None:
    """Daemon-teardown invariant: every stream stopped + cleaned (grabs released,
    children killed) before exit."""
    with _STREAMS_LOCK:
        streams = list(_STREAMS.values())
    for s in streams:
        s.stopped.set()
    for s in streams:
        s._thread.join(timeout=join_timeout)


def shutdown_pool() -> None:
    """Drop not-yet-started slow tasks and stop waiting on running ones so the daemon
    exits promptly on teardown instead of blocking on the pool's atexit join (10.0).
    Streams/children/grabs are already released by stop_all_streams(); a slow task
    abandoned here only loses its now-unwanted response (the panel is already gone)."""
    _POOL.shutdown(wait=False, cancel_futures=True)


@method("ping")
def _ping(params):
    return {"pong": True}
