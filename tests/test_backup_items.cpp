//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  test_backup_items.cpp  (deck-patches)
//
//  MadBackupItems emits the items[] array of every granular backup and restore.
//  These are the exact bytes that tell the daemon WHICH files to copy, and on a
//  restore, which files to copy OVER. There is no larger consequence available to
//  this panel than getting this array wrong.
//
//  Why this file exists: the phase 4b consolidation folded five pages onto shared
//  bases, and three of these writers moved in the process. The review's highest-risk
//  lens was exactly this - a payload that still parses but says something slightly
//  different backs up or restores the wrong thing, silently, with no compiler and
//  no on-screen symptom until the user opens a file that is not what they saved.
//  The shapes below are asserted against what the daemon actually parses.
//
//  Run:  cmake -DMAD_TESTS=on . && make -j4 mad-tests && ./tests/mad-tests -ts=backup-items
//

#include "doctest/doctest.h"

#include "guis/mad/MadBackupItems.h"

namespace
{
    // Emit through the real writers into the same shape production builds: the items
    // array lives inside the request's params object, so the test opens one too.
    std::string emit(const std::function<void(MadJson::Writer&)>& body)
    {
        rapidjson::StringBuffer buf;
        MadJson::Writer w {buf};
        w.StartObject();
        body(w);
        w.EndObject();
        return std::string {buf.GetString(), buf.GetSize()};
    }
} // namespace

TEST_SUITE("backup-items")
{
    TEST_CASE("a group-list backup names the group and the path")
    {
        const std::string json {emit([](MadJson::Writer& w) {
            w.Key("items");
            w.StartArray();
            MadBackupItems::writeOne(w, /*restore=*/false, "lightgun", "sinden/LightgunMono.exe.config");
            w.EndArray();
        })};
        CHECK(json ==
              R"({"items":[{"group":"lightgun","rel":"sinden/LightgunMono.exe.config"}]})");
    }

    TEST_CASE("a restore names the same file with the keys the restore side reads")
    {
        // Not a cosmetic difference: the backup side plans by group+rel, the restore
        // side resolves by system+id. Swapping them silently restores nothing.
        const std::string json {emit([](MadJson::Writer& w) {
            w.Key("items");
            w.StartArray();
            MadBackupItems::writeOne(w, /*restore=*/true, "lightgun", "sinden/LightgunMono.exe.config");
            w.EndArray();
        })};
        CHECK(json ==
              R"({"items":[{"system":"lightgun","id":"sinden/LightgunMono.exe.config"}]})");
    }

    TEST_CASE("BIOS items name the bucket and carry no group")
    {
        // BIOS files belong to no group. The key is omitted rather than sent empty,
        // which is safe because the daemon reads it as "group or other".
        const std::string json {emit([](MadJson::Writer& w) {
            MadBackupItems::writeTileItems(w, "bucket", "ps2", {{"", "bios/scph39001.bin"}});
        })};
        CHECK(json == R"({"items":[{"bucket":"ps2","rel":"bios/scph39001.bin"}]})");
        CHECK(json.find("group") == std::string::npos);
    }

    TEST_CASE("emulator config items name the emulator AND the group")
    {
        // The manifest tags each item with its group so a restore can regroup it.
        const std::string json {emit([](MadJson::Writer& w) {
            MadBackupItems::writeTileItems(w, "emulator", "pcsx2",
                                           {{"config", "PCSX2/inis/PCSX2.ini"}});
        })};
        CHECK(json ==
              R"({"items":[{"emulator":"pcsx2","group":"config","rel":"PCSX2/inis/PCSX2.ini"}]})");
    }

    TEST_CASE("the tile key names the tile, never the item's own group")
    {
        // Every item in one call belongs to the same tile; only the group varies.
        const std::string json {emit([](MadJson::Writer& w) {
            MadBackupItems::writeTileItems(w, "emulator", "dolphin",
                                           {{"config", "Dolphin/Config/Dolphin.ini"},
                                            {"saves", "Dolphin/StateSaves/a.sav"}});
        })};
        CHECK(json == R"({"items":[)"
                      R"({"emulator":"dolphin","group":"config","rel":"Dolphin/Config/Dolphin.ini"},)"
                      R"({"emulator":"dolphin","group":"saves","rel":"Dolphin/StateSaves/a.sav"}]})");
    }

    TEST_CASE("a tile restore keys every path off the one tile")
    {
        const std::string json {emit([](MadJson::Writer& w) {
            MadBackupItems::writeTileRestoreItems(w, "ps2", {"bios/scph39001.bin", "bios/scph39001.mec"});
        })};
        CHECK(json == R"({"items":[)"
                      R"({"system":"ps2","id":"bios/scph39001.bin"},)"
                      R"({"system":"ps2","id":"bios/scph39001.mec"}]})");
    }

    TEST_CASE("nothing ticked emits an empty array, not a missing key")
    {
        // The daemon iterates params["items"]; an absent key and an empty list must not
        // be allowed to diverge into "back up everything".
        const std::string backup {emit([](MadJson::Writer& w) {
            MadBackupItems::writeTileItems(w, "bucket", "ps2", {});
        })};
        const std::string restore {emit([](MadJson::Writer& w) {
            MadBackupItems::writeTileRestoreItems(w, "ps2", {});
        })};
        CHECK(backup == R"({"items":[]})");
        CHECK(restore == R"({"items":[]})");
    }

    TEST_CASE("paths with characters that need escaping survive the round trip")
    {
        // Real ROM and config names carry spaces, quotes and non-ASCII. The writer must
        // escape them and a parse must give the original path back, byte for byte.
        const std::string rel {R"(Sonic's "Best" Hits/conf ig.ini)"};
        const std::string json {emit([&rel](MadJson::Writer& w) {
            MadBackupItems::writeTileItems(w, "emulator", "flycast", {{"config", rel}});
        })};
        rapidjson::Document doc;
        REQUIRE(MadJson::parseLine(json, doc) == true);
        const rapidjson::Value& items {MadJson::getMember(doc, "items")};
        REQUIRE(items.IsArray());
        REQUIRE(items.Size() == 1);
        CHECK(MadJson::getString(items[0], "rel") == rel);
    }
}
