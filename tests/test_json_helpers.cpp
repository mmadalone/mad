//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  test_json_helpers.cpp  (deck-patches)
//
//  MadJson is the panel's entire view of the daemon: every RPC reply reaches a page
//  through getString / getBool / getInt / getInt64 / getMember, and every request
//  leaves through makeRequest.
//
//  Why this file exists: these accessors are what make the panel's JSON handling
//  safe-by-default. A page reads a field the daemon did not send on every error
//  path, so "absent" and "wrong type" must return the caller's default rather than
//  throw or read garbage - the panel has no exception handling to catch either. The
//  2026-08-12 audit flagged raw operator[] use as a hazard class precisely because
//  these helpers are the alternative.
//
//  Run:  cmake -DMAD_TESTS=on . && make -j4 mad-tests && ./tests/mad-tests -ts=json-helpers
//

#include "doctest/doctest.h"

#include "guis/mad/MadJson.h"

namespace
{
    // Parse through the real entry point the backend reader uses, so a test can never
    // assert against a document the production path would have rejected.
    rapidjson::Document parsed(const std::string& line)
    {
        rapidjson::Document doc;
        MadJson::parseLine(line, doc);
        return doc;
    }
} // namespace

TEST_SUITE("json-helpers")
{
    TEST_CASE("getString reads a present string and defaults otherwise")
    {
        const rapidjson::Document doc {parsed(R"({"message":"close PCSX2 first","rc":0})")};
        CHECK(MadJson::getString(doc, "message") == "close PCSX2 first");
        CHECK(MadJson::getString(doc, "absent") == "");
        CHECK(MadJson::getString(doc, "absent", "fallback") == "fallback");
        // Present but not a string: still the default, never a garbage read.
        CHECK(MadJson::getString(doc, "rc", "fallback") == "fallback");
    }

    TEST_CASE("getBool distinguishes absent from false")
    {
        const rapidjson::Document doc {parsed(R"({"done":true,"stopped":false,"rc":1})")};
        CHECK(MadJson::getBool(doc, "done") == true);
        CHECK(MadJson::getBool(doc, "stopped") == false);
        CHECK(MadJson::getBool(doc, "absent") == false);
        // A run stream's terminal is decided by these two flags, so a non-bool must
        // not read as true.
        CHECK(MadJson::getBool(doc, "rc") == false);
    }

    TEST_CASE("getInt and getInt64 default rather than throw")
    {
        const rapidjson::Document doc {parsed(R"({"restored":4,"size":5368709120,"name":"x"})")};
        CHECK(MadJson::getInt(doc, "restored", -1) == 4);
        CHECK(MadJson::getInt(doc, "absent", -1) == -1);
        CHECK(MadJson::getInt(doc, "name", -1) == -1);
        // Sizes exceed 32 bits routinely - a 5 GB backup must survive the round trip.
        CHECK(MadJson::getInt64(doc, "size", 0) == 5368709120LL);
        CHECK(MadJson::getInt64(doc, "absent", 7) == 7);
    }

    TEST_CASE("getMember returns a usable null for an absent member")
    {
        const rapidjson::Document doc {parsed(R"({"groups":[{"key":"a"}]})")};
        const rapidjson::Value& groups {MadJson::getMember(doc, "groups")};
        REQUIRE(groups.IsArray());
        CHECK(groups.Size() == 1);
        // The pages guard with IsArray() on the result, so an absent member must come
        // back as something safe to ask - not a dangling reference.
        const rapidjson::Value& missing {MadJson::getMember(doc, "absent")};
        CHECK(missing.IsArray() == false);
        CHECK(missing.IsObject() == false);
    }

    TEST_CASE("parseLine reports malformed input instead of half-parsing it")
    {
        rapidjson::Document doc;
        CHECK(MadJson::parseLine(R"({"ok":true})", doc) == true);
        rapidjson::Document bad;
        CHECK(MadJson::parseLine("{not json", bad) == false);
    }

    TEST_CASE("makeRequest emits the id, the method and the params the writer wrote")
    {
        const std::string line {MadJson::makeRequest(7, "granular.restore",
                                                     [](MadJson::Writer& w) {
                                                         w.Key("category");
                                                         w.String("bios");
                                                     })};
        const rapidjson::Document doc {parsed(line)};
        CHECK(MadJson::getInt(doc, "id", -1) == 7);
        CHECK(MadJson::getString(doc, "method") == "granular.restore");
        const rapidjson::Value& params {MadJson::getMember(doc, "params")};
        REQUIRE(params.IsObject());
        CHECK(MadJson::getString(params, "category") == "bios");
    }

    TEST_CASE("makeRequest with no params still produces a valid request")
    {
        // Half the panel's calls pass nullptr for the params writer (cloud.status,
        // backup.get_dest, sinden.install...), so that path must not emit broken JSON.
        const std::string line {MadJson::makeRequest(1, "cloud.status", nullptr)};
        rapidjson::Document doc;
        REQUIRE(MadJson::parseLine(line, doc) == true);
        CHECK(MadJson::getString(doc, "method") == "cloud.status");
    }
}
