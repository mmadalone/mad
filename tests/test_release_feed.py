"""The CI step that builds latest_release.json - ES-DE's in-app update feed.

The per-package "message" is the only place the update popup can say what changed;
it used to be "" so the popup was a bare one-liner. Filling it from commit subjects
means untrusted text reaches a step that holds a contents:write token, so the
generator is tested directly rather than trusted.

The python source is EXTRACTED FROM THE REAL WORKFLOW (the house pattern from
tests/test_postupdate_flag.py) so there is no copied snippet to drift out of sync.

Four hazards, each made structurally impossible rather than filtered:
  * the old unquoted `cat <<EOF` ran parameter expansion and command substitution
    on commit text; the quoted heredoc means the shell never sees it at all
  * `"` in a subject (16 of 212 in the real history) -> json.dump escapes it
  * multibyte subjects (27 of 212) vs ES-DE's 280-BYTE substr -> ASCII-only makes a
    mid-codepoint cut, and its U+FFFD glyph, unreachable
  * GuiMsgBox never clamps its height -> a hard cap keeps the buttons on screen

Run:  python3 -m unittest tests.test_release_feed -v
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "build-appimage.yml"

# ES-DE truncates the message with std::string::substr, which counts BYTES. Raised from 280
# to 1200 on 2026-08-14 (es-app/src/ApplicationUpdater.cpp): 280 was chosen when GuiMsgBox
# grew without limit, but it clamps its height and scrolls now, so the cap had stopped
# protecting the layout and started deciding how much changelog you were allowed to read.
# THIS FILE IS THE ONLY TEST OF THAT GENERATOR. The workflow is mirrored on both branches
# and only `main` runs a test over it, so this constant and the fork's must move together.
ESDE_TRUNCATE_BYTES = 1200


def _extract_generator() -> str:
    text = WORKFLOW.read_text()
    m = re.search(r"python3 - > latest_release\.json <<'PY'\n(.*?)\n\s*PY\n",
                  text, re.S)
    assert m, "could not find the feed generator in build-appimage.yml"
    return textwrap.dedent(m.group(1))


class ReleaseFeed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _extract_generator()

    def _gen(self, subjects, run="161"):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as fh:
            fh.write("\n".join(subjects))
            path = fh.name
        try:
            env = {**os.environ, "RUN": run, "MD5": "d41d8cd98f00b204e9800998ecf8427e",
                   "DATE": "2026-08-06", "SUBJECTS": path}
            r = subprocess.run(["python3", "-c", self.src], capture_output=True,
                               text=True, env=env, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)
        finally:
            os.unlink(path)

    def _msg(self, doc):
        return doc["stable"]["packages"][0]["message"]

    # --- schema ----------------------------------------------------------------
    def test_schema_matches_what_es_de_parses(self):
        doc = self._gen(["a change"])
        self.assertEqual(set(doc), {"stable", "prerelease"})
        pkg = doc["stable"]["packages"][0]
        self.assertEqual(pkg["name"], "LinuxSteamDeckAppImage")
        self.assertEqual(pkg["filename"], "ES-DE-MAD.AppImage")
        self.assertIn("latest-steamdeck", pkg["url"])
        self.assertEqual(doc["stable"]["release"], "161")
        self.assertEqual(doc["stable"]["version"], "3.4.1-mad.161")
        self.assertEqual(doc["prerelease"]["packages"], [])

    def test_message_is_not_at_the_document_root(self):
        # A top-level "message" is read as a GitHub-API ERROR and aborts the whole
        # update check silently, with no popup at all.
        doc = self._gen(["a change"])
        self.assertNotIn("message", doc)
        self.assertNotIn("message", doc["stable"])

    # --- hostile input ---------------------------------------------------------
    def test_quotes_backticks_and_substitution_are_inert(self):
        doc = self._gen(['MAD backup P9: "All" tile (systems grid)',
                         "fix `uname` and $(id) and $HOME handling",
                         r"escape a back\slash"])
        msg = self._msg(doc)
        self.assertNotIn("uid=", msg)          # $(id) never ran
        self.assertNotIn(os.environ.get("HOME", "/home"), msg)
        json.dumps(doc)                        # round-trips

    def test_output_is_pure_ascii(self):
        doc = self._gen(["em dash — and ellipsis … and CJK 日本語 and emoji 🎮",
                         "accented café naïve"])
        msg = self._msg(doc)
        msg.encode("ascii")                    # raises if not
        self.assertNotIn("�", msg)

    def test_truncation_is_a_provable_no_op_for_es_de(self):
        # If our own cap holds, ES-DE's byte substr can never cut anything.
        doc = self._gen(["x" * 400, "y" * 400, "z" * 400, "w" * 400])
        msg = self._msg(doc)
        encoded = msg.encode("utf-8")
        self.assertLessEqual(len(encoded), ESDE_TRUNCATE_BYTES)
        self.assertEqual(msg.encode("utf-8")[:ESDE_TRUNCATE_BYTES].decode("utf-8"), msg)

    def test_line_budget_keeps_the_buttons_on_screen(self):
        # GuiMsgBox honours \n as hard breaks and never clamps its height.
        doc = self._gen([f"change number {i}" for i in range(20)])
        msg = self._msg(doc)
        # KEEP items, so KEEP-1 separators. Was 3 lines + a "Build N:" header; the header is
        # gone (ES-DE prints the version directly above it) and KEEP rose 3 -> 6 to match the
        # standing 4-to-6 changelog rule. Build 167 had four commits and could only show three.
        self.assertLessEqual(msg.count("\n"), 5)
        for line in msg.split("\n"):
            # "- " + MAX_LINE + the "..." an over-long subject gets
            self.assertLessEqual(len(line), 81)

    def test_control_characters_are_stripped(self):
        doc = self._gen(["tab\there and newline-ish\r carriage", "bell\x07char"])
        msg = self._msg(doc)
        for bad in ("\t", "\r", "\x07"):
            self.assertNotIn(bad, msg)

    # --- degenerate cases ------------------------------------------------------
    def test_empty_log_yields_a_valid_document_with_no_message(self):
        doc = self._gen([])
        self.assertEqual(self._msg(doc), "",
                         "a lone header says nothing; leave the popup as it was")
        self.assertEqual(doc["stable"]["release"], "161")

    def test_blank_subjects_are_dropped(self):
        doc = self._gen(["", "   ", "real change"])
        self.assertIn("real change", self._msg(doc))
        # One item, so NO newline. It used to be 1 because the "Build N:" header supplied it.
        self.assertEqual(self._msg(doc).count("\n"), 0)

    def test_missing_subjects_file_still_produces_valid_json(self):
        env = {**os.environ, "RUN": "9", "MD5": "x", "DATE": "2026-08-06",
               "SUBJECTS": "/nonexistent/subjects.txt"}
        r = subprocess.run(["python3", "-c", self.src], capture_output=True,
                           text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["stable"]["packages"][0]["message"], "")

    def test_message_does_NOT_name_the_build(self):
        # ES-DE prints "NEW RELEASE AVAILABLE: 3.4.1-mad.205" immediately above this text, so
        # a "Build 205:" header repeated the number on the next line and, with only three
        # items, spent a third of the changelog saying nothing. Reported from a screenshot
        # 2026-08-14.
        msg = self._msg(self._gen(["something"], run="205"))
        self.assertNotIn("Build 205", msg)
        self.assertTrue(msg.startswith("- "), msg)

    def test_the_item_count_is_chosen_not_inherited(self):
        # The guard used to be `len(lines) > KEEP` with `lines` SEEDED by the header. Dropping
        # the header without changing it to `>=` would have silently allowed KEEP + 1.
        for n in (0, 1, 5, 6, 7, 50):
            msg = self._msg(self._gen([f"subject {i}" for i in range(n)]))
            items = len(msg.split("\n")) if msg else 0
            self.assertEqual(items, min(n, 6), f"{n} subjects -> {items} items")


class WorkflowShape(unittest.TestCase):
    def test_yaml_still_parses(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not available")
        doc = yaml.safe_load(WORKFLOW.read_text())
        steps = doc["jobs"]["build"]["steps"]
        names = [s.get("name") for s in steps]
        self.assertIn("Generate latest_release.json", names)

    def test_heredoc_is_quoted(self):
        # An unquoted heredoc would expand commit text in the shell, inside a step
        # holding a contents:write token.
        text = WORKFLOW.read_text()
        self.assertIn("<<'PY'", text)
        self.assertNotIn("cat > latest_release.json <<EOF", text)


if __name__ == "__main__":
    unittest.main()
