//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  test_background_game_end.cpp  (deck-patches)
//
//  BackgroundGameEndWatch decides when a Steam game launched from ES-DE has really ended, so
//  its game-end can fire without waiting for the user's first input. Firing early (while the
//  game still runs) would run the cleanup hooks mid-game; firing never just keeps today's
//  first-input behaviour. These cases replay the sequences recorded on the Deck on 2026-10-29
//  (deck-docs/gamescope-focus.md) at ES-DE's real cadence: ~16 ms passes when focused, ~33 ms
//  when unfocused, and NO passes at all while PauseGames has ES-DE SIGSTOPped.
//
//  Run:  cmake -DMAD_TESTS=on . && make -j4 mad-tests && ./tests/mad-tests -ts=background-game-end
//

#include "doctest/doctest.h"

#include "BackgroundGameEndWatch.h"
#include "SteamReaper.h"

#include <algorithm>
#include <cstdint>
#include <string>
#include <vector>

namespace
{
    using W = BackgroundGameEndWatch;

    // Appids recorded on the Deck.
    constexpr unsigned long kEsde {4278202385UL}; // ES-DE itself (a non-Steam shortcut)
    constexpr unsigned long kGiJoe {1432220UL}; // a Steam game
    constexpr unsigned long kDeadpool {3250635768UL}; // a non-Steam shortcut game

    // Drives the watch exactly like main.cpp: one sample per loop pass, a reaper scan only when
    // the watch asks for one, and FIRE clears the launch (handleBackgroundGameReturn()).
    struct Driver {
        W watch;
        std::uint64_t now {1000};
        unsigned int serial {1};
        bool launched {true};
        W::Focus focus {W::Focus::FOCUSED};
        bool uiIdle {true};
        std::vector<unsigned long> reapers {kEsde};
        std::vector<W::Event> events;
        std::vector<std::uint64_t> fireTimes;
        std::vector<unsigned long> fireGames; // the game ids the watch held when it fired
        bool keepLaunchedAfterFire {false}; // simulate a caller that does NOT clear the launch

        W::Event pass()
        {
            W::Sample s;
            s.nowMs = now;
            s.launched = launched;
            s.launchSerial = serial;
            s.focus = focus;
            s.uiIdle = uiIdle;
            s.selfAppId = kEsde;
            if (watch.wantsReaperScan(launched, serial, now)) {
                s.reapersScanned = true;
                s.reapers = reapers;
            }
            const W::Event event {watch.update(s)};
            if (event != W::Event::NONE)
                events.push_back(event);
            if (event == W::Event::FIRE) {
                fireTimes.push_back(now);
                fireGames = watch.gameAppIds();
                if (!keepLaunchedAfterFire)
                    launched = false;
            }
            return event;
        }

        // Loop passes every stepMs for durationMs.
        void run(std::uint64_t durationMs, std::uint64_t stepMs)
        {
            const std::uint64_t end {now + durationMs};
            while (now < end) {
                pass();
                now += stepMs;
            }
        }

        // ES-DE SIGSTOPped by PauseGames: time passes, no loop passes happen.
        void freeze(std::uint64_t durationMs) { now += durationMs; }

        bool saw(W::Event event) const
        {
            return std::find(events.cbegin(), events.cend(), event) != events.cend();
        }
    };

    // The G.I. Joe run of 2026-10-29 (times relative to the launch at 01:23:45.0).
    // reaperAtMs: when Steam's reaper for the game appears (not recorded; the worst case is
    // shortly before the game window, the last moment ES-DE still runs).
    void replaySteamRun(Driver& d, unsigned long game, std::uint64_t reaperAtMs)
    {
        d.run(350, 16); // ES-DE still in front for 0.35 s
        d.focus = W::Focus::UNFOCUSED; // Steam UI 769 takes focus
        d.run(reaperAtMs - 350, 33);
        d.reapers = {kEsde, game};
        d.run(10737 - reaperAtMs, 33); // until PauseGames freezes ES-DE (0.4 s after game focus)
        d.freeze(93430 - 10737); // frozen while playing; the game exits at ~91.35 s
        d.reapers = {kEsde}; // reaper gone with the game
        d.focus = W::Focus::FOCUSED; // ES-DE got focus at 93.23 s, resumed at 93.43 s
    }

    std::string cmdline(const std::vector<std::string>& argv)
    {
        std::string out;
        for (const std::string& arg : argv)
            out.append(arg).push_back('\0');
        return out;
    }
} // namespace

TEST_SUITE("background-game-end")
{
    TEST_CASE("a normal Steam quit fires once, after the hold, never before ES-DE is back")
    {
        Driver d;
        replaySteamRun(d, kGiJoe, 3000);
        CHECK(d.saw(W::Event::ARMED));
        CHECK(d.saw(W::Event::SEEN));
        CHECK(d.fireTimes.empty()); // nothing fired during launch or while frozen

        const std::uint64_t resumedAt {d.now};
        d.run(2000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        CHECK(d.saw(W::Event::GONE));
        // The hold starts after the resume, so the fire comes kHoldMs later.
        CHECK(d.fireTimes[0] >= resumedAt + W::kHoldMs);
        CHECK(d.fireTimes[0] <= resumedAt + W::kHoldMs + 2 * 16);
    }

    TEST_CASE("the game reaper is still seen when it appears just before the freeze")
    {
        // Worst case from the recording: the game is visible only 237 ms before PauseGames
        // freezes ES-DE. The armed scan cadence (100 ms) must still catch it.
        Driver d;
        replaySteamRun(d, kGiJoe, 10500);
        CHECK(d.saw(W::Event::SEEN));
        d.run(2000, 16);
        CHECK(d.fireTimes.size() == 1);
    }

    TEST_CASE("a non-Steam shortcut game behaves the same")
    {
        Driver d;
        replaySteamRun(d, kDeadpool, 3000);
        d.run(2000, 16);
        CHECK(d.fireTimes.size() == 1);
        CHECK(d.fireGames == std::vector<unsigned long> {kDeadpool});
    }

    TEST_CASE("switching back to ES-DE while the game still runs does not fire")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(3000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(8000, 33);
        REQUIRE(d.saw(W::Event::SEEN));

        d.focus = W::Focus::FOCUSED; // Steam button -> ES-DE, game still running
        d.run(10000, 16);
        CHECK(d.fireTimes.empty());

        d.focus = W::Focus::UNFOCUSED; // back to the game, then quit it
        d.run(5000, 33);
        d.reapers = {kEsde};
        d.run(2000, 33); // Steam UI shows for ~2 s
        d.focus = W::Focus::FOCUSED;
        d.run(1000, 16);
        CHECK(d.fireTimes.size() == 1);
    }

    TEST_CASE("a launch whose game never appears never fires, and stops watching after the deadline")
    {
        Driver d;
        d.run(60000, 16); // e.g. a Kodi/desktop background launch, or Steam failed to start it
        CHECK(d.fireTimes.empty());
        CHECK_FALSE(d.saw(W::Event::SEEN));

        d.run(W::kSeenDeadlineMs, 100);
        CHECK(d.saw(W::Event::SEEN_TIMEOUT));
        CHECK_FALSE(d.watch.wantsReaperScan(true, d.serial, d.now + 1000));
        d.reapers = {kEsde, kGiJoe}; // a Steam game started from Steam's library later on
        d.run(5000, 100);
        d.reapers = {kEsde};
        d.run(5000, 16);
        CHECK(d.fireTimes.empty());
    }

    TEST_CASE("a focus flicker shorter than the hold does not fire")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.reapers = {kEsde};
        d.run(1000, 33);

        d.focus = W::Focus::FOCUSED;
        d.run(200, 16);
        d.focus = W::Focus::UNFOCUSED;
        d.run(100, 33);
        CHECK(d.fireTimes.empty());

        d.focus = W::Focus::FOCUSED;
        const std::uint64_t regainedAt {d.now};
        d.run(1000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        CHECK(d.fireTimes[0] >= regainedAt + W::kHoldMs);
    }

    TEST_CASE("a gap between samples restarts the hold")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.reapers = {kEsde};
        d.run(500, 33); // the game is gone while the Steam UI still has focus
        REQUIRE(d.saw(W::Event::GONE));
        d.focus = W::Focus::FOCUSED;
        d.run(200, 16); // 200 ms of hold, short of kHoldMs
        REQUIRE(d.fireTimes.empty());
        d.freeze(W::kMaxSampleGapMs + 50); // Steam button: PauseGames freezes ES-DE
        const std::uint64_t resumedAt {d.now};
        d.run(1000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        CHECK(d.fireTimes[0] >= resumedAt + W::kHoldMs);
    }

    TEST_CASE("when the first input handles the launch, the watch disarms and never fires")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.launched = false; // handleBackgroundGameReturn() ran from input()
        d.run(100, 16);
        CHECK(d.saw(W::Event::DISARMED));
        d.reapers = {kEsde};
        d.focus = W::Focus::FOCUSED;
        d.run(2000, 16);
        CHECK(d.fireTimes.empty());
    }

    TEST_CASE("a second launch re-arms with a new baseline")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.focus = W::Focus::FOCUSED; // back in ES-DE, G.I. Joe still running
        d.run(1000, 16);

        d.serial = 2; // launch Deadpool while G.I. Joe still runs
        d.run(100, 16);
        CHECK(std::count(d.events.cbegin(), d.events.cend(), W::Event::ARMED) == 2);
        CHECK(d.watch.baseline() == std::vector<unsigned long> {kEsde, kGiJoe});

        d.focus = W::Focus::UNFOCUSED;
        d.reapers = {kEsde, kGiJoe, kDeadpool};
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe}; // Deadpool exits, G.I. Joe keeps running
        d.focus = W::Focus::FOCUSED;
        d.run(1000, 16);
        CHECK(d.fireTimes.size() == 1);
        CHECK(d.fireGames == std::vector<unsigned long> {kDeadpool});
    }

    TEST_CASE("it fires once per launch even if the caller does not clear the launch")
    {
        Driver d;
        d.keepLaunchedAfterFire = true;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.reapers = {kEsde};
        d.focus = W::Focus::FOCUSED;
        d.run(1000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        d.reapers = {kEsde, kGiJoe}; // the same game comes and goes again
        d.run(1000, 16);
        d.reapers = {kEsde};
        d.run(2000, 16);
        CHECK(d.fireTimes.size() == 1);
    }

    TEST_CASE("ES-DE's own reaper is never the game, even when missing from the baseline")
    {
        Driver d;
        d.reapers = {}; // the baseline scan did not see ES-DE's reaper
        d.run(100, 16);
        REQUIRE(d.saw(W::Event::ARMED));
        d.reapers = {kEsde};
        d.run(2000, 16);
        CHECK_FALSE(d.saw(W::Event::SEEN));
    }

    TEST_CASE("an unknown focus verdict never fires")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.reapers = {kEsde};
        d.focus = W::Focus::UNKNOWN; // gamescope unavailable / identity not learned / poll failed
        d.run(5000, 16);
        CHECK(d.fireTimes.empty());
    }

    TEST_CASE("a game already running before the launch is ignored")
    {
        Driver d;
        d.reapers = {kEsde, kGiJoe}; // ES-DE launched a game Steam already runs
        d.run(30000, 16);
        CHECK_FALSE(d.saw(W::Event::SEEN));
        d.reapers = {kEsde};
        d.run(5000, 16);
        CHECK(d.fireTimes.empty());
    }

    TEST_CASE("an open menu delays the fire until it closes")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.reapers = {kEsde};
        d.focus = W::Focus::FOCUSED;
        d.uiIdle = false; // user opened the main menu right away
        d.run(3000, 16);
        CHECK(d.fireTimes.empty());
        d.uiIdle = true;
        const std::uint64_t closedAt {d.now};
        d.run(1000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        CHECK(d.fireTimes[0] >= closedAt + W::kHoldMs);
    }

    TEST_CASE("Steam UI and appid 0 are never taken for the game")
    {
        Driver d;
        d.reapers = {kEsde};
        d.run(100, 16);
        d.reapers = {kEsde, 769, 0};
        d.run(1000, 16);
        CHECK_FALSE(d.saw(W::Event::SEEN));
    }

    TEST_CASE("arming waits for a reaper scan, and the scan cadence follows the state")
    {
        W watch;
        W::Sample s;
        s.launched = true;
        s.launchSerial = 1;
        s.nowMs = 1000;
        CHECK(watch.wantsReaperScan(true, 1, 1000));
        CHECK_FALSE(watch.wantsReaperScan(false, 1, 1000));
        CHECK(watch.update(s) == W::Event::NONE); // no scan yet: no baseline, not armed

        s.reapersScanned = true;
        s.reapers = {kEsde};
        CHECK(watch.update(s) == W::Event::ARMED);
        CHECK_FALSE(watch.wantsReaperScan(true, 1, 1000 + W::kScanIntervalArmedMs - 1));
        CHECK(watch.wantsReaperScan(true, 1, 1000 + W::kScanIntervalArmedMs));
        CHECK(watch.wantsReaperScan(true, 2, 1001)); // a new launch scans at once

        s.nowMs = 1100;
        s.reapers = {kEsde, kGiJoe};
        CHECK(watch.update(s) == W::Event::SEEN);
        CHECK_FALSE(watch.wantsReaperScan(true, 1, 1100 + W::kScanIntervalArmedMs));
        CHECK(watch.wantsReaperScan(true, 1, 1100 + W::kScanIntervalSeenMs));
    }

    TEST_CASE("the reaper command-line parser")
    {
        unsigned long app {0};
        const std::string reaper {"/home/deck/.local/share/Steam/ubuntu12_32/reaper"};

        CHECK(SteamReaper::parseCmdline(
            cmdline({reaper, "SteamLaunch", "AppId=1432220", "--", "/home/deck/game.sh"}), app));
        CHECK(app == kGiJoe);

        CHECK(SteamReaper::parseCmdline(cmdline({reaper, "SteamLaunch", "AppId=4278202385", "--",
                                                 "/home/deck/Applications/ES-DE.AppImage"}),
                                        app));
        CHECK(app == kEsde);

        CHECK(SteamReaper::parseCmdline(
            cmdline({"reaper", "SteamLaunch", "-flag", "AppId=7", "--", "x"}), app));
        CHECK(app == 7);

        CHECK_FALSE(SteamReaper::parseCmdline(
            cmdline({"/usr/bin/bash", "SteamLaunch", "AppId=5", "--", "x"}), app));
        CHECK_FALSE(SteamReaper::parseCmdline(cmdline({reaper, "Other", "AppId=5", "--", "x"}), app));
        // An AppId after "--" belongs to the game's own command line.
        CHECK_FALSE(
            SteamReaper::parseCmdline(cmdline({reaper, "SteamLaunch", "--", "AppId=5"}), app));
        CHECK_FALSE(
            SteamReaper::parseCmdline(cmdline({reaper, "SteamLaunch", "AppId=12a", "--"}), app));
        CHECK_FALSE(SteamReaper::parseCmdline(cmdline({reaper, "SteamLaunch", "AppId=", "--"}), app));
        CHECK_FALSE(SteamReaper::parseCmdline(cmdline({reaper, "SteamLaunch", "AppId=0", "--"}), app));
        CHECK_FALSE(SteamReaper::parseCmdline(
            cmdline({reaper, "SteamLaunch", "AppId=4294967296", "--"}), app));
        CHECK_FALSE(SteamReaper::parseCmdline(
            cmdline({reaper, "SteamLaunch", "AppId=12345678901", "--"}), app));
        CHECK_FALSE(SteamReaper::parseCmdline(cmdline({reaper, "SteamLaunch"}), app));
        CHECK_FALSE(SteamReaper::parseCmdline("", app)); // kernel threads (oom_reaper) have none
    }
}
