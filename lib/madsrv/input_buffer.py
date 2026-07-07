"""Shared buffered-editor state machine for MAD input/config pages.

Generalises the per-emulator buffers that already power the buffered X=Save /
Y=Cancel pages (ragamein/ragameset in retroarch_game_cmds.py, pcsx2_pergame_cmds,
lindbergh_cmds) so a new buffered backend is just: supply three callbacks over an
opaque WORKING COPY and wire four thin RPC methods (get/set/save/cancel).

The working copy is opaque: an INI/TOML text string (eden, pcsx2, xemu) or a nested
dict (ryujinx). dirty is a deep `!=` compare against a deepcopy snapshot of disk, so
the SAME rule works for both representations.

Invariants enforced here so a hand-clone can't quietly forget them:
  * dirty = (working != disk), recomputed after every set.
  * disk is a deepcopy snapshot (nested JSON snapshots independently of working).
  * get() keeps the buffer only when it is for the SAME ctx AND currently dirty;
    otherwise it reloads fresh from disk.
  * set() keeps the buffer for the same ctx (edits accumulate); it reloads only on a
    ctx change or first use.
  * save() is guarded on (ctx match AND non-empty edits); flush replays the staged
    edits; staterev("config") is bumped exactly once after a successful flush.
  * ctx is the CONFIG-FILE IDENTITY (a tuple like () for a global file, (titleid,) or
    (titleid, core) for per-game). NEVER put the Player stepper in ctx: the whole-file
    working copy already spans every player; player is only a render filter.

CALLER CONTRACT (the one thing this module cannot enforce at the decorator):
  the buffered `*_get` RPC must be registered WITHOUT `cache=("config",)`. During
  buffered editing nothing writes to disk, so "config" never bumps, so a rev-cache would
  serve the stale pre-edit snapshot and hide every staged edit. This buffer's own
  reload-unless-dirty guard IS the cache. Register buffered getters `@method(name,
  slow=True)` only.
"""

import copy
import threading


class InputBuffer:
    """A single in-memory editing buffer, keyed on config-file identity (ctx).

    Callbacks (all supplied by the owning module):
      load(ctx) -> working
          Read the config from disk and return the working copy (str or dict).
      apply_edit(working, edit) -> (working, staged)
          Apply one edit to the working copy and return the (possibly new) working copy
          plus an opaque `staged` record that flush() can replay. `edit` is whatever the
          module passes to set() (typically a dict of key/kind/player/value).
      flush(ctx, disk, edits) -> fresh_working
          Persist the staged edits to disk (atomically) and return the post-save working
          copy read back from disk. Use the replay strategy: re-read fresh disk and apply
          only THESE edits, so a foreign concurrent edit to other keys survives. Do NOT
          bump staterev here; save() bumps once after you return.
    """

    def __init__(self, *, load, apply_edit, flush, copyfn=copy.deepcopy,
                 bump_key="config"):
        self._load = load
        self._apply_edit = apply_edit
        self._flush = flush
        self._copy = copyfn
        self._bump_key = bump_key
        self._lock = threading.RLock()
        self._loaded = False
        self.ctx = None
        self.working = None
        self.disk = None
        self.edits = []
        self.dirty = False

    # -- internal ---------------------------------------------------------------

    def _reload(self, ctx):
        working = self._load(ctx)
        self.ctx = ctx
        self.working = working
        self.disk = self._copy(working)
        self.edits = []
        self.dirty = False
        self._loaded = True

    def _holds(self, ctx):
        return self._loaded and self.ctx == ctx and self.working is not None

    # -- public verbs -----------------------------------------------------------

    def get(self, ctx):
        """Return the working copy for ctx. Keeps staged edits only when this buffer is
        already on ctx AND dirty; otherwise reloads fresh from disk."""
        with self._lock:
            if not (self._holds(ctx) and self.dirty):
                self._reload(ctx)
            return self.working

    def set(self, ctx, edit):
        """Stage one edit (in memory only). Returns the opaque staged record. Reloads
        first only if the buffer is for a different ctx (or unused)."""
        with self._lock:
            if not self._holds(ctx):
                self._reload(ctx)
            self.working, staged = self._apply_edit(self.working, edit)
            self.edits.append(staged)
            self.dirty = (self.working != self.disk)
            return staged

    def save(self, ctx):
        """Flush staged edits to disk and clear the buffer's dirty state. Returns True if
        a write happened, False if there was nothing to save (or ctx mismatch)."""
        with self._lock:
            if not self._holds(ctx):
                # A different ctx than the one we hold: not ours to save. Leave the held
                # buffer (working/edits/dirty) intact so its staged edits are not stranded.
                return False
            if not self.edits:
                self.dirty = False
                return False
            fresh = self._flush(ctx, self.disk, list(self.edits))
            self.working = fresh
            self.disk = self._copy(fresh)
            self.edits = []
            self.dirty = False
            from .. import staterev
            staterev.bump(self._bump_key)
            return True

    def cancel(self, ctx):
        """Discard staged edits by reloading from disk."""
        with self._lock:
            self._reload(ctx)
            return True

    def reset(self):
        """Drop all buffered state without touching disk. The next verb reloads fresh.
        For tests that repoint the underlying config file between cases."""
        with self._lock:
            self._loaded = False
            self.ctx = None
            self.working = None
            self.disk = None
            self.edits = []
            self.dirty = False
