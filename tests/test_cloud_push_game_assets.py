"""cloud.push_game_assets RPC: the CLOUD side of the game-first per-asset backup ("Back up a game" ->
MEGA). It resolves a game's ticked asset groups via the SHARED planner granular_backup.plan_game_assets,
persists a plan-dir (a NUL src/rel list + the multi-category manifest) under the daemon state dir, then
streams deck-cloud.sh push-games - an opaque-rel transport, so per-asset rels
(saves/..., media/..., roms/...) upload unchanged.

Locks in the safety-critical contracts (no rclone/network - the shell engine is mocked out):
  * a valid selection persists <state>/games-plan/<ts>/{mad-manifest.json, plan} and streams push-games,
    with EVERY asset rel written into the NUL plan (proves per-asset paths flow through the shell verbatim);
  * an EMPTY or ALL-SKIPPED selection is an EINVAL (so the C++ releases its synchronous mRunning guard);
  * a concurrent cloud op is EBUSY, and a rejected start does NOT orphan the plan dir;
  * a per-asset upload rides the shared push-games subcommand (not a restore; same op title).

Run:  python3 -m unittest tests.test_cloud_push_game_assets -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import backup_manifest as bm                  # noqa: E402
from lib.madsrv import cloud_cmds as cc                 # noqa: E402
from lib.madsrv.rpc import RpcError                     # noqa: E402


def _fake_asset_plan(_games, ts, emit=None, is_stopped=None):
    """A canned (manifest, plan) spanning TWO categories (a save + the ROM) so the test needs no on-disk
    library and can prove that a non-roms rel (saves/...) rides the shell transport exactly like roms/..."""
    m = bm.new_manifest("granular", created=ts)
    bm.add_item(m, category="saves", category_label="Saves", system="gba",
                system_label="Game Boy Advance",
                item=bm.make_item(id="saves/retroarch/saves/Emerald.srm", name="Pokemon Emerald",
                                  src="/live/saves/retroarch/saves/Emerald.srm",
                                  rel="saves/retroarch/saves/Emerald.srm", kind="file", size=32,
                                  extra={"game": "gba:Emerald", "asset": "saves"}))
    bm.add_item(m, category="roms", category_label="ROMs & games", system="gba",
                system_label="Game Boy Advance",
                item=bm.make_item(id="roms/gba/Emerald.gba", name="Pokemon Emerald",
                                  src="/live/roms/gba/Emerald.gba", rel="roms/gba/Emerald.gba",
                                  kind="file", size=64, extra={"game": "gba:Emerald", "asset": "rom"}))
    plan = [
        {"id": "saves/retroarch/saves/Emerald.srm", "name": "Pokemon Emerald", "system": "gba",
         "category": "saves", "src": "/live/saves/retroarch/saves/Emerald.srm",
         "rel": "saves/retroarch/saves/Emerald.srm", "kind": "file"},
        {"id": "roms/gba/Emerald.gba", "name": "Pokemon Emerald", "system": "gba", "category": "roms",
         "src": "/live/roms/gba/Emerald.gba", "rel": "roms/gba/Emerald.gba", "kind": "file"},
    ]
    return m, plan


class PushGameAssets(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["DECK_CLOUD_STATE_DIR"] = str(self.tmp / "state")

    def tearDown(self):
        os.environ.pop("DECK_CLOUD_STATE_DIR", None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_persists_plan_dir_and_streams(self):
        seen = {}

        def fake_stream_op(argv):
            seen["argv"] = argv
            return {"stream": "s1"}

        items = [{"system": "gba", "stem": "Emerald", "keys": ["rom", "saves"]}]
        # _run rc 3 = rclone "not found" -> no remote set yet, so the push writes a FRESH manifest.
        # (A transport failure rc - 1/5/... - deliberately ABORTS rather than clobber the set index.)
        with mock.patch.object(cc.granular_backup, "plan_game_assets", _fake_asset_plan), \
             mock.patch.object(cc, "_run", lambda *a, **k: (3, "", "")), \
             mock.patch.object(cc, "_stream_op", fake_stream_op):
            out = cc._cloud_push_game_assets({"items": items})
        self.assertEqual(out, {"stream": "s1"})
        argv = seen["argv"]
        # the "push-games" shell subcommand + fixed "games" token - a per-asset backup accumulates into
        # one single games set.
        self.assertEqual(argv[0:2], [str(cc.ENGINE), "push-games"])
        self.assertEqual(argv[2], "games")
        plandir = Path(argv[3])
        self.assertEqual(plandir.parent, cc._state_dir() / "games-plan")
        self.assertTrue((plandir / "mad-manifest.json").is_file(), "manifest persisted for the engine")
        # BOTH rels present in the NUL plan, in order - a saves/... rel and a roms/... rel upload alike.
        self.assertEqual(
            (plandir / "plan").read_bytes(),
            b"/live/saves/retroarch/saves/Emerald.srm\0saves/retroarch/saves/Emerald.srm\0"
            b"/live/roms/gba/Emerald.gba\0roms/gba/Emerald.gba\0",
            "the NUL src/rel plan carries every asset path verbatim for the shell")

    def test_empty_items_einval(self):
        with self.assertRaises(RpcError) as cm:
            cc._cloud_push_game_assets({"items": []})
        self.assertEqual(cm.exception.code, "EINVAL")

    def test_nothing_present_einval(self):
        # a game whose ticked assets are all absent -> empty plan -> EINVAL (the C++ mRunning guard releases)
        with mock.patch.object(cc.granular_backup, "plan_game_assets",
                               lambda *a, **k: (bm.new_manifest("granular", created="x"), [])):
            with self.assertRaises(RpcError) as cm:
                cc._cloud_push_game_assets({"items": [{"system": "gba", "stem": "Ghost", "keys": ["rom"]}]})
        self.assertEqual(cm.exception.code, "EINVAL")

    def test_a_busy_engine_queues_the_push_and_keeps_its_plan(self):
        """Queueing replaced rejection (user 2026-07-31): a second backup WAITS its turn instead of
        being refused. Its plan dir must SURVIVE - the dispatcher hands that exact directory to the
        engine when its turn comes, so deleting it here would queue a job that cannot run."""
        self.assertTrue(cc._RUN_ACTIVE.acquire(blocking=False))
        try:
            with mock.patch.object(cc.granular_backup, "plan_game_assets", _fake_asset_plan):
                out = cc._cloud_push_game_assets(
                    {"items": [{"system": "gba", "stem": "Emerald", "keys": ["rom"]}]})
        finally:
            cc._RUN_ACTIVE.release()
        self.assertIn("queued", out, f"expected a queued reply, got {out}")
        self.assertGreaterEqual(out["position"], 1)
        pd = cc._state_dir() / "games-plan"
        self.assertTrue(pd.is_dir() and list(pd.iterdir()),
                        "the queued job's plan dir must be kept for the dispatcher")

    def test_asset_upload_is_not_a_restore(self):
        # rides the shared push-games subcommand: auto-resumes as an upload, no restore-confirm gate.
        self.assertFalse(cc._is_restore(["push-games", "20260726T000000", "/x/plan"]))
        self.assertEqual(cc._op_title(["push-games"]), "Backing up games")


if __name__ == "__main__":
    unittest.main()
