"""routing.load_policy re-asserts base router_skip after the local merge.

A system whose BASE policy ships router_skip=true is a documented hands-off system;
a stray local `router_skip=false` must never re-enable routing for it at the launch
path (mirrors the panel-side write clamp in madsrv/policy_cmds.py). RetroArch-hub
plan, phase 0.
"""
import tempfile
import unittest
from pathlib import Path

from lib import routing


class RouterSkipGuardTest(unittest.TestCase):
    def _load(self, base_toml: str, local_toml: str) -> dict:
        with tempfile.TemporaryDirectory() as d:
            bp = Path(d) / "base.toml"
            lp = Path(d) / "local.toml"
            bp.write_text(base_toml, encoding="utf-8")
            lp.write_text(local_toml, encoding="utf-8")
            orig_b, orig_l = routing.POLICY_FILE, routing.LOCAL_POLICY_FILE
            routing.POLICY_FILE, routing.LOCAL_POLICY_FILE = bp, lp
            try:
                return routing.load_policy()
            finally:
                routing.POLICY_FILE, routing.LOCAL_POLICY_FILE = orig_b, orig_l

    def test_local_cannot_unskip_base_hands_off_system(self):
        pol = self._load(
            "[systems.ps2]\nrouter_skip = true\n",
            "[systems.ps2]\nrouter_skip = false\n",
        )
        self.assertIs(pol["systems"]["ps2"].get("router_skip"), True)

    def test_local_can_still_set_skip_on_non_base_system(self):
        pol = self._load(
            "[systems.nes]\ncategory = \"console\"\n",
            "[systems.nes]\nrouter_skip = true\n",
        )
        self.assertIs(pol["systems"]["nes"].get("router_skip"), True)

    def test_base_non_skip_stays_off(self):
        pol = self._load("[systems.nes]\ncategory = \"console\"\n", "")
        self.assertFalse(pol["systems"]["nes"].get("router_skip", False))

    def test_local_scalar_alias_does_not_crash(self):
        # local clobbers a base hands-off system's table with a scalar alias; the
        # re-assert guard must not raise (fail-soft "broken local never breaks routing").
        pol = self._load("[systems.ps2]\nrouter_skip = true\n",
                         "[systems]\nps2 = \"arcade\"\n")
        self.assertIsInstance(pol, dict)


if __name__ == "__main__":
    unittest.main()
