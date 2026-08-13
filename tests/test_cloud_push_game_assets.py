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

import json
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

        def fake_stream_op(argv, **_kw):   # **_kw: _persist_games_plan_and_stream now
            # passes queue_if_busy/merge_cmd/plan_dir, and a fake with a rigid
            # signature would break on any future one too.
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

    def test_becoming_busy_DURING_the_manifest_merge_queues_instead_of_refusing(self):
        """THE RACE (reported 2026-08-13: "shouldn't it have been queued?").

        _persist_games_plan_and_stream decides run-now vs queue with _is_busy(), then does a MEGA
        manifest merge (a network round trip, seconds), then calls _stream_op - which re-checks and
        used to RAISE, because it was called without queue_if_busy. So anything that started during
        that window turned an intended queue into a user-visible refusal, and the except below it
        then deleted the plan dir, throwing the work away too. The owner hit exactly this: pressing
        "back up all games" while a BIOS backup still held the engine produced
        "EBUSY: a cloud backup/restore is already running" and no job at all.

        Simulated at the real seam rather than by patching the outcome: the merge itself grabs the
        engine, which is precisely what a second op doing the same thing would do.

        _cloud_push_game_assets and _cloud_push_game_assets_all share this helper, so one test
        covers both entry points.
        """
        def busy_arrives_during_the_merge(*_a, **_k):
            cc._RUN_ACTIVE.acquire(blocking=False)     # another op takes the engine mid-merge
            return (3, "", "")                          # rc 3 = no remote set yet, fresh manifest
        try:
            with mock.patch.object(cc.granular_backup, "plan_game_assets", _fake_asset_plan), \
                 mock.patch.object(cc, "_run", busy_arrives_during_the_merge):
                out = cc._cloud_push_game_assets(
                    {"items": [{"system": "gba", "stem": "Emerald", "keys": ["rom"]}]})
        finally:
            try:
                cc._RUN_ACTIVE.release()
            except RuntimeError:
                pass
        self.assertIn("queued", out, f"a late-arriving busy must QUEUE, not refuse; got {out}")
        pd = cc._state_dir() / "games-plan"
        self.assertTrue(pd.is_dir() and list(pd.iterdir()),
                        "the queued job's plan dir must survive: the dispatcher needs that exact "
                        "directory, and the old EBUSY path deleted it")
        # The two arguments whose loss would be SILENT and catastrophic. Without merge_cmd the
        # dispatcher publishes an index containing only this selection, REPLACING the remote one:
        # the bytes of everything previously uploaded survive on MEGA, the record of them does not.
        # Nothing else in the suite pins that this call site passes them.
        j = cc._registry().queued_jobs()[0]
        self.assertEqual(j.get("merge_cmd"), "cat-manifest",
                         "a queued set push must carry its merge command to dispatch")
        self.assertEqual(j.get("plan_dir"), str(next(pd.iterdir())))

    def test_a_late_queue_leaves_the_plan_manifest_unmerged(self):
        """A queued job is merged AGAIN at dispatch, so the run-now merge must be undone.

        Measured, after I first claimed a double merge was equivalent: backup_manifest.merge sets
        updated = incoming.created, so merging an already-merged file stamps the set with its BIRTH
        date rather than this backup's, and the panel's restore picker both shows and SORTS on that.
        Worse, if the set is deleted while the job waits, dispatch finds no remote and would publish
        the pre-merged file as the index of files that no longer exist.
        """
        remote = bm.new_manifest("granular", created="20260101T000000")
        bm.add_item(remote, category="roms", category_label="R", system="gba", system_label="GBA",
                    item=bm.make_item(id="gba:Old", name="Old", src="/r/Old.gba",
                                      rel="roms/gba/Old.gba", kind="file", size=1))
        def busy_arrives_during_the_merge(*_a, **_k):
            cc._RUN_ACTIVE.acquire(blocking=False)
            return (0, json.dumps(remote), "")
        try:
            with mock.patch.object(cc.granular_backup, "plan_game_assets", _fake_asset_plan), \
                 mock.patch.object(cc, "_run", busy_arrives_during_the_merge):
                out = cc._cloud_push_game_assets(
                    {"items": [{"system": "gba", "stem": "Emerald", "keys": ["rom"]}]})
        finally:
            try:
                cc._RUN_ACTIVE.release()
            except RuntimeError:
                pass
        self.assertIn("queued", out)
        pd = next((cc._state_dir() / "games-plan").iterdir())
        left = bm.read(bm.manifest_path(pd))
        rels = [it["rel"] for c in (left.get("categories") or {}).values()
                for s in (c.get("systems") or {}).values() for it in (s.get("items") or [])]
        self.assertNotIn("roms/gba/Old.gba", rels,
                         "the remote's items must NOT be baked into a queued job's manifest; "
                         "dispatch merges, and doing it twice corrupts the set date and can "
                         "republish purged items")


if __name__ == "__main__":
    unittest.main()
