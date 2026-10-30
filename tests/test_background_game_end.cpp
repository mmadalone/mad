//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  test_background_game_end.cpp  (deck-patches)
//
//  BackgroundGameEndWatch decides when a Steam game launched from ES-DE is over for the user,
//  so its game-end can fire without waiting for the user's first input. Firing while the game
//  still runs is only right after a real switch-back to ES-DE (the cleanup then runs mid-game,
//  as upstream's first input did); firing never just keeps today's first-input behaviour. These
//  cases replay the sequences recorded on the Deck on 2026-10-29 (deck-docs/gamescope-focus.md)
//  at ES-DE's real cadence: ~16 ms passes when focused, ~33 ms when unfocused, and NO passes at
//  all while PauseGames has ES-DE SIGSTOPped.
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

    // Drives the watch exactly like main.cpp: one sample per loop pass carrying the reapers
    // listed just before the launch command (`before`, from Window), a reaper scan only when the
    // watch asks for one, and FIRE clears the launch (handleBackgroundGameReturn()).
    struct Driver {
        W watch;
        std::uint64_t now {1000};
        unsigned int serial {1};
        bool launched {true};
        std::vector<unsigned long> before {kEsde}; // listed just before the launch command
        bool beforeOk {true};
        std::string rom {"/home/deck/ROMs/steam/G.I.\\ Joe.sh"}; // game-end param 1
        W::Focus focus {W::Focus::FOCUSED};
        bool uiIdle {true};
        std::vector<unsigned long> reapers {kEsde};
        int scans {0};
        int missScans {0}; // the next scans list only ES-DE's reaper (a failed cmdline read)
        std::uint64_t missedScanAt {0}; // when the last missed scan happened
        std::vector<W::Event> events;
        std::vector<std::uint64_t> fireTimes;
        std::vector<unsigned long> fireGames; // the game ids the watch held when it fired
        bool lastFireWhileRunning {false}; // firedWhileGameRunning() at the last FIRE
        std::vector<std::uint64_t> trailedGoneTimes; // when TRAILED_GAME_GONE came
        std::vector<std::uint64_t> trailedEndedTimes; // when TRAILED_GAME_ENDED came
        bool lastEndedAfterReturn {false}; // endedAfterReturn() at the last TRAILED_GAME_ENDED
        bool keepLaunchedAfterFire {false}; // simulate a caller that does NOT clear the launch

        W::Event pass()
        {
            W::Sample s;
            s.nowMs = now;
            s.launched = launched;
            s.launchSerial = serial;
            s.baselineOk = beforeOk;
            s.baseline = before;
            s.launchRom = rom;
            s.focus = focus;
            s.uiIdle = uiIdle;
            s.selfAppId = kEsde;
            if (watch.wantsReaperScan(launched, serial, now)) {
                s.reapersScanned = true;
                s.reapers = reapers;
                if (missScans > 0) {
                    s.reapers = {kEsde};
                    --missScans;
                    missedScanAt = now;
                }
                ++scans;
            }
            const W::Event event {watch.update(s)};
            if (event != W::Event::NONE)
                events.push_back(event);
            if (event == W::Event::TRAILED_GAME_GONE)
                trailedGoneTimes.push_back(now);
            if (event == W::Event::TRAILED_GAME_ENDED) {
                trailedEndedTimes.push_back(now);
                lastEndedAfterReturn = watch.endedAfterReturn(); // main.cpp reads it right away
            }
            if (event == W::Event::FIRE) {
                fireTimes.push_back(now);
                fireGames = watch.gameAppIds();
                lastFireWhileRunning = watch.firedWhileGameRunning();
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
    // reaperAtMs: when Steam's reaper for the game appears (test 2 measured 1.4-3 s for a Steam
    // game, ~0.5 s for a non-Steam shortcut; the worst case is shortly before the game window,
    // the last moment ES-DE still runs).
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

    TEST_CASE("switching back to ES-DE while the game still runs fires once, after the longer hold")
    {
        // Test 2 on 2026-10-29, step 6: nothing fired here, and the launch splash (holding
        // keyboard focus, so SDL dropped every press) stayed up until its own 60 s timer.
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(3000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(8000, 33);
        REQUIRE(d.saw(W::Event::SEEN));

        d.freeze(20000); // playing; PauseGames has ES-DE frozen
        d.focus = W::Focus::FOCUSED; // Steam button -> ES-DE, game still running
        const std::uint64_t backAt {d.now};
        d.run(5000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        CHECK(d.lastFireWhileRunning);
        CHECK(d.fireTimes[0] >= backAt + W::kSwitchBackHoldMs);
        CHECK(d.fireTimes[0] <= backAt + W::kSwitchBackHoldMs + 2 * 16);

        d.focus = W::Focus::UNFOCUSED; // back to the game, then quit it
        d.run(5000, 33);
        d.reapers = {kEsde};
        d.run(2000, 33); // Steam UI shows for ~2 s
        d.focus = W::Focus::FOCUSED;
        d.run(1000, 16);
        CHECK(d.fireTimes.size() == 1); // the cleanup already ran at the switch-back
        CHECK(d.trailedGoneTimes.size() == 1); // but A is ignored briefly once ES-DE is back
    }

    TEST_CASE("after a switch-back, the game's later exit guards A once ES-DE is back in front")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.focus = W::Focus::FOCUSED; // switched back: the cleanup fires after the hold
        d.run(2000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        REQUIRE_FALSE(d.launched); // the fire handled the launch
        const int scansAfterFire {d.scans};

        d.focus = W::Focus::UNFOCUSED; // back to the game
        d.run(3000, 33);
        CHECK(d.scans > scansAfterFire); // still following the game, no launch pending
        d.reapers = {kEsde}; // quit it; Steam's UI shows for ~2 s first
        d.run(2000, 33);
        CHECK(d.trailedGoneTimes.empty()); // not before ES-DE is in front
        d.focus = W::Focus::FOCUSED;
        const std::uint64_t backAt {d.now};
        d.run(2000, 16);
        REQUIRE(d.trailedGoneTimes.size() == 1);
        CHECK(d.trailedGoneTimes[0] == backAt);
        CHECK(d.fireTimes.size() == 1);
        CHECK_FALSE(d.watch.wantsReaperScan(false, d.serial, d.now + 1000)); // done
    }

    TEST_CASE("after a switch-back, the game's exit is confirmed once, on the scan after")
    {
        // The pending game-end (MAD.172) fires on TRAILED_GAME_ENDED: never while the game runs,
        // never before ES-DE is back in front, exactly once.
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.focus = W::Focus::FOCUSED; // switched back
        d.run(2000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        REQUIRE(d.watch.followingGame());

        d.focus = W::Focus::UNFOCUSED; // back to the game, then quit it
        d.run(3000, 33);
        d.freeze(60000);
        d.reapers = {kEsde};
        d.run(2000, 33); // Steam's UI first
        CHECK(d.trailedEndedTimes.empty());
        d.focus = W::Focus::FOCUSED;
        const std::uint64_t backAt {d.now};
        d.run(3000, 16);
        REQUIRE(d.trailedGoneTimes.size() == 1);
        REQUIRE(d.trailedEndedTimes.size() == 1);
        CHECK(d.trailedEndedTimes[0] > backAt);
        CHECK(d.trailedEndedTimes[0] <= backAt + W::kScanIntervalSeenMs + 2 * 16);
        CHECK(d.lastEndedAfterReturn); // quit from the game: a return from it
        CHECK_FALSE(d.watch.followingGame());
    }

    TEST_CASE("while following in front, a missed scan never confirms an exit, the real exit does")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.focus = W::Focus::FOCUSED; // switched back, ES-DE stays in front
        d.run(2000, 16);
        REQUIRE(d.watch.followingGame());

        d.missScans = 1; // one scan misses the live game
        d.run(2000, 16);
        CHECK(d.trailedEndedTimes.empty());
        CHECK(d.watch.followingGame());

        d.reapers = {kEsde}; // the game exits while ES-DE never left the front
        d.run(2000, 16);
        CHECK(d.trailedGoneTimes.empty()); // no return from the game: A is not paused
        CHECK(d.trailedEndedTimes.size() == 1); // but its pending game-end must still run
        CHECK_FALSE(d.lastEndedAfterReturn); // the caller keeps key releases and A
    }

    TEST_CASE("an open menu delays the confirmed exit until it closes")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.focus = W::Focus::FOCUSED; // switched back
        d.run(2000, 16);
        REQUIRE(d.watch.followingGame());

        d.uiIdle = false; // a menu is open when the game exits
        d.reapers = {kEsde};
        d.run(3000, 16);
        CHECK(d.trailedEndedTimes.empty());
        CHECK(d.watch.followingGame());
        d.uiIdle = true; // closed: the next scan confirms
        const std::uint64_t closedAt {d.now};
        d.run(1000, 16);
        REQUIRE(d.trailedEndedTimes.size() == 1);
        CHECK(d.trailedEndedTimes[0] <= closedAt + W::kScanIntervalSeenMs + 2 * 16);
    }

    TEST_CASE("launchedGameRunning() is true only while the seen game still runs, before a fire")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        CHECK_FALSE(d.watch.launchedGameRunning()); // armed, no game seen yet
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        CHECK(d.watch.launchedGameRunning());
        d.focus = W::Focus::FOCUSED; // switched back: fires, then follows the game
        d.run(2000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        CHECK_FALSE(d.watch.launchedGameRunning());

        Driver q; // a quit instead
        q.focus = W::Focus::UNFOCUSED;
        q.run(1000, 33);
        q.reapers = {kEsde, kGiJoe};
        q.run(1000, 33);
        q.reapers = {kEsde};
        q.run(500, 33);
        CHECK_FALSE(q.watch.launchedGameRunning()); // gone: a return now is a quit
    }

    TEST_CASE("relaunching the game a switch-back left running takes it as the launch's game")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.focus = W::Focus::FOCUSED; // switched back: the cleanup fires, G.I. Joe keeps running
        d.run(2000, 16);
        REQUIRE(d.fireTimes.size() == 1);

        d.serial = 2; // A on G.I. Joe again: Steam refocuses the running game
        d.launched = true;
        d.before = {kEsde, kGiJoe};
        d.run(W::kSwitchBackHoldMs + 500, 16); // Steam is slow to refocus it
        CHECK(d.watch.gameAppIds() == std::vector<unsigned long> {kGiJoe});
        CHECK(d.fireTimes.size() == 1); // ES-DE never left the front in this launch: no fire
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.freeze(30000); // playing
        d.reapers = {kEsde}; // quit
        d.focus = W::Focus::FOCUSED;
        d.run(2000, 16);
        REQUIRE(d.fireTimes.size() == 2);
        CHECK_FALSE(d.lastFireWhileRunning);
        CHECK(d.fireGames == std::vector<unsigned long> {kGiJoe});
    }

    TEST_CASE("a shortcut whose reaper starts before the watch arms is still seen")
    {
        // Test 2 on 2026-10-29, step 5 (Deadpool): Steam started the shortcut's reaper ~0.5 s
        // after the launch command, while ES-DE was still saving the gamelist, and the watch
        // armed ~1 s after the launch. It is still new against the list taken BEFORE the launch.
        Driver d;
        d.reapers = {kEsde, kDeadpool}; // already up at the first scan
        d.focus = W::Focus::UNFOCUSED; // Steam UI took focus 0.35 s after the launch
        d.run(10000, 33);
        REQUIRE(d.saw(W::Event::SEEN));
        d.freeze(15000); // playing
        d.reapers = {kEsde}; // quit via the Steam menu
        d.focus = W::Focus::FOCUSED;
        const std::uint64_t resumedAt {d.now};
        d.run(2000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        CHECK_FALSE(d.lastFireWhileRunning);
        CHECK(d.fireGames == std::vector<unsigned long> {kDeadpool});
        CHECK(d.fireTimes[0] <= resumedAt + W::kHoldMs + 2 * 16);
    }

    TEST_CASE("a return to ES-DE shorter than the switch-back hold does not fire while the game runs")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.focus = W::Focus::FOCUSED; // a glance at ES-DE, then straight back to the game
        d.run(W::kSwitchBackHoldMs - 200, 16);
        d.focus = W::Focus::UNFOCUSED;
        d.run(3000, 33);
        CHECK(d.fireTimes.empty());

        d.reapers = {kEsde}; // quit later: the quit still fires
        d.focus = W::Focus::FOCUSED;
        d.run(1000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        CHECK_FALSE(d.lastFireWhileRunning);
    }

    TEST_CASE("while ES-DE never left the front, a running game does not fire but its exit does")
    {
        Driver d; // focus stays FOCUSED throughout: ES-DE was never away
        d.run(500, 16);
        d.reapers = {kEsde, kGiJoe};
        d.run(5000, 16);
        REQUIRE(d.saw(W::Event::SEEN));
        CHECK(d.fireTimes.empty());
        d.reapers = {kEsde};
        d.run(1000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        CHECK_FALSE(d.lastFireWhileRunning);
    }

    TEST_CASE("one scan that misses the live game during a short return does not fire")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        REQUIRE(d.saw(W::Event::SEEN));
        d.focus = W::Focus::FOCUSED; // a glance at ES-DE...
        d.run(500, 16);
        d.missScans = 1; // ...during which one scan misses the game's reaper
        d.run(300, 16);
        d.focus = W::Focus::UNFOCUSED; // back to the game before the switch-back hold is over
        d.run(3000, 33);
        CHECK(d.fireTimes.empty());
    }

    TEST_CASE("a missed scan during a switch-back hold costs a confirming scan, not a quit fire")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.focus = W::Focus::FOCUSED; // switched back, staying in ES-DE
        d.run(500, 16);
        d.missScans = 1;
        d.run(3000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        CHECK(d.lastFireWhileRunning);
        CHECK(d.fireTimes[0] >= d.missedScanAt + W::kScanIntervalSeenMs + W::kSwitchBackHoldMs);
    }

    TEST_CASE("a first input that handles a launch whose game still runs keeps following it")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.focus = W::Focus::FOCUSED; // switched back, the splash already gone
        d.run(500, 16);
        d.launched = false; // a press within the switch-back hold: the first-input path fired
        d.run(100, 16);
        REQUIRE(d.saw(W::Event::DISARMED));
        CHECK(d.fireTimes.empty());
        CHECK(d.watch.followingGame());

        d.focus = W::Focus::UNFOCUSED; // back to the game, quit it later
        d.run(3000, 33);
        d.reapers = {kEsde};
        d.run(2000, 33);
        d.focus = W::Focus::FOCUSED;
        d.run(1000, 16);
        CHECK(d.trailedGoneTimes.size() == 1);
        CHECK(d.trailedEndedTimes.size() == 1); // the game-end the first press deferred runs
        CHECK(d.lastEndedAfterReturn);
        CHECK_FALSE(d.watch.followingGame());
    }

    TEST_CASE("while ES-DE stays in front, neither a missed scan nor the game's own exit pauses A")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.focus = W::Focus::FOCUSED; // switched back: the cleanup fires, ES-DE stays in front
        d.run(2000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        REQUIRE(d.watch.followingGame());

        d.missScans = 1;
        d.run(1000, 16);
        CHECK(d.trailedGoneTimes.empty()); // the user never left ES-DE: no return to guard
        CHECK(d.watch.followingGame()); // and the next scan found the game again
        d.reapers = {kEsde}; // the game exits on its own (closed slowly, or crashed)
        d.run(1000, 16);
        CHECK(d.trailedGoneTimes.empty());
        CHECK_FALSE(d.watch.followingGame()); // a second scan confirmed it
    }

    TEST_CASE("a missed scan just before going back to the game does not end following it")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.focus = W::Focus::FOCUSED; // switched back: the cleanup fires
        d.run(2000, 16);
        REQUIRE(d.watch.followingGame());

        d.missScans = 1;
        while (d.missedScanAt == 0) { // in front until the missed scan
            d.pass();
            d.now += 16;
        }
        d.focus = W::Focus::UNFOCUSED; // right after it, back to the game
        d.run(50, 33);
        d.freeze(30000); // playing, then quit
        d.reapers = {kEsde};
        d.pass(); // the first pass after the resume still has the old focus verdict
        d.now += 16;
        CHECK(d.watch.followingGame());
        d.focus = W::Focus::FOCUSED;
        const std::uint64_t backAt {d.now};
        d.run(1000, 16);
        REQUIRE(d.trailedGoneTimes.size() == 1); // the return is still guarded
        CHECK(d.trailedGoneTimes[0] == backAt);
        CHECK_FALSE(d.watch.followingGame());
    }

    TEST_CASE("a first input after the game has exited does not start following it")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.reapers = {kEsde};
        d.run(500, 33); // gone while Steam's UI has the front
        REQUIRE(d.saw(W::Event::GONE));
        d.focus = W::Focus::FOCUSED;
        d.run(100, 16); // inside the quit hold
        d.launched = false; // a first input handled it before the automatic fire
        d.pass();
        CHECK(d.events.back() == W::Event::DISARMED);
        CHECK_FALSE(d.watch.followingGame());
        d.now += 16;
        d.run(2000, 16);
        CHECK(d.trailedGoneTimes.empty());
        CHECK(d.fireTimes.empty());
    }

    TEST_CASE("a relaunch after the followed game has exited is armed normally")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.focus = W::Focus::FOCUSED; // switched back: the cleanup fires
        d.run(2000, 16);
        REQUIRE(d.fireTimes.size() == 1);

        d.focus = W::Focus::UNKNOWN; // no focus verdict: the exit below is not reported yet
        d.reapers = {kEsde};
        d.run(1000, 16);
        REQUIRE(d.watch.followingGame());
        d.serial = 2; // the same game again, but it no longer runs
        d.launched = true;
        d.before = {kEsde};
        d.run(100, 16);
        CHECK(d.watch.gameAppIds().empty());
        CHECK_FALSE(d.watch.followingGame());
    }

    TEST_CASE("a menu or a freeze during the switch-back hold restarts it")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.focus = W::Focus::FOCUSED; // switched back, the game still runs
        d.run(800, 16);
        d.uiIdle = false; // a menu opens before the hold is over
        d.run(2000, 16);
        CHECK(d.fireTimes.empty());
        d.uiIdle = true;
        d.run(800, 16);
        d.freeze(W::kMaxSampleGapMs + 50); // Steam button: PauseGames freezes ES-DE briefly
        const std::uint64_t resumedAt {d.now};
        d.run(2000, 16);
        REQUIRE(d.fireTimes.size() == 1);
        CHECK(d.fireTimes[0] >= resumedAt + W::kSwitchBackHoldMs);
        CHECK(d.lastFireWhileRunning);
    }

    TEST_CASE("an unknown focus verdict never counts as ES-DE having been away")
    {
        Driver d;
        d.focus = W::Focus::UNKNOWN; // e.g. gamescope polls failing while Steam had the front
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        REQUIRE(d.saw(W::Event::SEEN));
        d.focus = W::Focus::FOCUSED; // the game still runs
        d.run(3000, 16);
        CHECK(d.fireTimes.empty());
    }

    TEST_CASE("having been away in one launch does not carry over to the next")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED; // launch 1: Steam UI, then the first input handles it
        d.run(1000, 33);
        d.launched = false;
        d.run(100, 16);

        d.serial = 2; // launch 2: ES-DE never leaves the front, the game keeps running
        d.launched = true;
        d.focus = W::Focus::FOCUSED;
        d.run(500, 16);
        d.reapers = {kEsde, kGiJoe};
        d.run(5000, 16);
        REQUIRE(d.saw(W::Event::SEEN));
        CHECK(d.fireTimes.empty());
    }

    TEST_CASE("after a launch that is not watched, the next launch is watched again")
    {
        Driver d;
        d.beforeOk = false;
        d.run(100, 16);
        REQUIRE(d.saw(W::Event::NO_BASELINE));
        d.launched = false; // the first input handled it
        d.run(100, 16);

        d.serial = 2; // the next launch lists its reapers fine
        d.launched = true;
        d.beforeOk = true;
        replaySteamRun(d, kGiJoe, 3000);
        d.run(2000, 16);
        CHECK(d.saw(W::Event::ARMED));
        CHECK(d.fireTimes.size() == 1);
    }

    TEST_CASE("a launch whose reapers could not be listed beforehand is not watched")
    {
        Driver d;
        d.beforeOk = false; // SteamReaper::listAppIds() failed just before the launch command
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.reapers = {kEsde};
        d.focus = W::Focus::FOCUSED;
        d.run(3000, 16);
        REQUIRE(d.events.size() == 1);
        CHECK(d.events[0] == W::Event::NO_BASELINE);
        CHECK(d.scans == 0);
        CHECK(d.fireTimes.empty());
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

    TEST_CASE("a second launch re-arms with its own pre-launch list")
    {
        Driver d;
        d.focus = W::Focus::UNFOCUSED;
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe};
        d.run(1000, 33);
        d.focus = W::Focus::FOCUSED; // back in ES-DE, G.I. Joe still running
        d.run(2000, 16);
        REQUIRE(d.fireTimes.size() == 1); // the switch-back ran G.I. Joe's cleanup
        CHECK(d.lastFireWhileRunning);

        d.serial = 2; // then the user launches Deadpool while G.I. Joe still runs
        d.launched = true;
        d.before = {kEsde, kGiJoe};
        d.rom = "/home/deck/ROMs/steam/Deadpool.sh";
        d.run(100, 16);
        CHECK(std::count(d.events.cbegin(), d.events.cend(), W::Event::ARMED) == 2);
        CHECK(d.watch.baseline() == std::vector<unsigned long> {kEsde, kGiJoe});
        CHECK(d.watch.gameAppIds().empty()); // G.I. Joe is not this launch's game

        d.focus = W::Focus::UNFOCUSED;
        d.reapers = {kEsde, kGiJoe, kDeadpool};
        d.run(1000, 33);
        d.reapers = {kEsde, kGiJoe}; // Deadpool exits, G.I. Joe keeps running
        d.focus = W::Focus::FOCUSED;
        d.run(1000, 16);
        REQUIRE(d.fireTimes.size() == 2);
        CHECK(d.fireGames == std::vector<unsigned long> {kDeadpool});
        CHECK_FALSE(d.lastFireWhileRunning);
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
        d.before = {}; // the list before the launch did not include ES-DE's reaper
        d.run(100, 16);
        REQUIRE(d.saw(W::Event::ARMED));
        d.run(2000, 16); // the scans do see it
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
        d.before = {kEsde, kGiJoe}; // ES-DE launched a game Steam already runs
        d.reapers = {kEsde, kGiJoe};
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

    TEST_CASE("arming takes the pre-launch list, scans at once, then follows the state's cadence")
    {
        W watch;
        W::Sample s;
        s.launched = true;
        s.launchSerial = 1;
        s.baselineOk = true;
        s.baseline = {kEsde};
        s.nowMs = 1000;
        CHECK_FALSE(watch.wantsReaperScan(true, 1, 1000)); // not armed yet: no scan needed
        CHECK(watch.update(s) == W::Event::ARMED);
        CHECK(watch.baseline() == std::vector<unsigned long> {kEsde});
        CHECK(watch.wantsReaperScan(true, 1, 1000)); // the first scan comes at once
        CHECK_FALSE(watch.wantsReaperScan(false, 1, 1000));
        CHECK_FALSE(watch.wantsReaperScan(true, 2, 1000)); // a newer launch is armed first

        s.nowMs = 1016;
        s.reapersScanned = true;
        s.reapers = {kEsde};
        CHECK(watch.update(s) == W::Event::NONE);
        CHECK_FALSE(watch.wantsReaperScan(true, 1, 1016 + W::kScanIntervalArmedMs - 1));
        CHECK(watch.wantsReaperScan(true, 1, 1016 + W::kScanIntervalArmedMs));

        s.nowMs = 1116;
        s.reapers = {kEsde, kGiJoe};
        CHECK(watch.update(s) == W::Event::SEEN);
        CHECK_FALSE(watch.wantsReaperScan(true, 1, 1116 + W::kScanIntervalArmedMs));
        CHECK(watch.wantsReaperScan(true, 1, 1116 + W::kScanIntervalSeenMs));

        s.launchSerial = 2; // the next launch is armed from its own list
        s.reapersScanned = false;
        s.baseline = {kEsde, kGiJoe};
        CHECK(watch.update(s) == W::Event::ARMED);
        CHECK(watch.baseline() == std::vector<unsigned long> {kEsde, kGiJoe});
        CHECK(watch.wantsReaperScan(true, 2, 1116)); // its first scan comes at once too

        s.launched = false; // the first input handled launch 2
        CHECK(watch.update(s) == W::Event::DISARMED);
        s.launched = true; // the launched state again, with the same serial: not a new launch
        CHECK(watch.update(s) == W::Event::NONE);
        CHECK_FALSE(watch.wantsReaperScan(true, 2, 1116 + 1000));
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

    TEST_CASE("the launch-script parser maps steam://rungameid to the reaper's AppId")
    {
        unsigned long app {0};
        const std::string url {"steam://rungameid/"};

        // A Steam game: the id is the AppId.
        CHECK(SteamReaper::parseRungameid("exec steam steam://rungameid/1432220\n", app));
        CHECK(app == kGiJoe);

        // A non-Steam shortcut: (AppId << 32) | 0x02000000. Deadpool's real ROM file.
        app = 0;
        CHECK(SteamReaper::parseRungameid(
            "#!/bin/sh\n# Deadpool\n# Launched via Steam so its configured Proton / prefix / "
            "launch options apply.\nexec steam steam://rungameid/13961374314801397760\n",
            app));
        CHECK(app == kDeadpool);
        // The largest shortcut AppId: (0xFFFFFFFF << 32) | 0x02000000.
        app = 0;
        CHECK(SteamReaper::parseRungameid(url + "18446744069448138752", app));
        CHECK(app == 4294967295UL);

        // CRLF line ends, a //args suffix, and only the first URL counts.
        app = 0;
        CHECK(SteamReaper::parseRungameid("exec steam steam://rungameid/1432220\r\n", app));
        CHECK(app == kGiJoe);
        app = 0;
        CHECK(SteamReaper::parseRungameid("steam steam://rungameid/1432220//-windowed", app));
        CHECK(app == kGiJoe);
        app = 0;
        CHECK(SteamReaper::parseRungameid(
            "steam://rungameid/1432220 steam://rungameid/13961374314801397760", app));
        CHECK(app == kGiJoe);

        // Not a Steam launch, or an id with no known reaper AppId: false, app left alone.
        app = 99;
        CHECK_FALSE(SteamReaper::parseRungameid("", app));
        CHECK_FALSE(SteamReaper::parseRungameid("exec /usr/bin/pcsx2 game.chd", app));
        CHECK_FALSE(SteamReaper::parseRungameid(url, app));
        CHECK_FALSE(SteamReaper::parseRungameid(url + "abc", app));
        CHECK_FALSE(SteamReaper::parseRungameid(url + "0", app));
        // A mod, type 1: (5 << 32) | (1 << 24) | 220.
        CHECK_FALSE(SteamReaper::parseRungameid(url + "21491613916", app));
        // Type 0 with high bits set is no Steam game id: (5 << 32) | 220.
        CHECK_FALSE(SteamReaper::parseRungameid(url + "21474836700", app));
        // A shortcut id whose AppId part is 0: 0x02000000.
        CHECK_FALSE(SteamReaper::parseRungameid(url + "33554432", app));
        // The largest 64-bit id is type 0xFF; one more does not fit in 64 bits.
        CHECK_FALSE(SteamReaper::parseRungameid(url + "18446744073709551615", app));
        CHECK_FALSE(SteamReaper::parseRungameid(url + "18446744073709551616", app));
        CHECK_FALSE(SteamReaper::parseRungameid(url + "99999999999999999999999", app));
        // 2^64 + 1432220: without the overflow check this would wrap around to G.I. Joe.
        CHECK_FALSE(SteamReaper::parseRungameid(url + "18446744073710983836", app));
        CHECK(app == 99);
    }
}
