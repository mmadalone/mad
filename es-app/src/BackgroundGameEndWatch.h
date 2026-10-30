//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  BackgroundGameEndWatch.h  (deck-patches)
//
//  Decides WHEN to fire the game-end event of a game launched with %RUNINBACKGROUND% (the Steam
//  system), so the main loop can fire it without waiting for the user's first input.
//
//  Upstream fires that game-end "on application wakeup" (INSTALL.md), i.e. on the first input
//  after the user comes back. On the Steam Deck that meant: the launch splash lingering, the
//  first press eaten (the A filter) or frozen for seconds while the game-end scripts ran, and
//  the cleanup hooks (cloud resume/push, power, controller router) not running at all until a
//  button was pressed - one recorded run fired 7 minutes after the game had exited. And while
//  the splash is up it holds keyboard focus, so SDL drops every controller press before ES-DE
//  sees it: until the splash's own 60 s timer, there was no first input to wait for.
//
//  The signals (recorded on the Deck 2026-10-29, see deck-docs/gamescope-focus.md):
//    * game alive  = Steam's launcher `reaper SteamLaunch AppId=<id> -- ...` for that game is
//      running. The reaper lives until every process of the game has exited (Steam's own
//      "Running" state); the PauseGames Decky plugin finds games the same way.
//    * ES-DE in front = GamescopeFocus's STRICT verdict (Unknown when it cannot tell).
//
//  Rule:  ARMED at a successful background launch, baseline = the reapers listed just BEFORE
//         its launch command ran (Steam starts a non-Steam shortcut's reaper within ~0.5 s,
//         before ES-DE's main loop is back, so a list taken after the launch can already hold
//         the game)
//         -> SEEN when a reaper AppId outside the baseline appears (within kSeenDeadlineMs)
//         -> FIRE once ES-DE is positively in front with no menu open, continuously for
//            OBSERVED time: kHoldMs after every seen reaper has exited (the game was quit), or
//            kSwitchBackHoldMs while it still runs if ES-DE was away (unfocused) since arming
//            (the user switched back to ES-DE, or came back during Steam's launch phase). That
//            runs the cleanup while the game keeps running in the background, as upstream's
//            first input did; it is also what frees the user from the launch splash.
//         -> after a switch-back FIRE (or a first input that handled the launch while its game
//            still runs), TRAILING: follow that game to its real exit, then (ES-DE in front)
//            TRAILED_GAME_GONE, so the caller briefly ignores A as after a quit; meanwhile the
//            caller keeps the screensaver from counting the time the game has the front.
//            Relaunching that same game meanwhile takes its running reapers as the new
//            launch's game (a game that already runs gets no new reaper). This is a fallback:
//            ViewController::triggerGameLaunch() refuses to start a Steam launch script whose
//            game is still running, because Steam only answers "Game already running".
//  Anything the watch cannot establish leaves today's first-input path in charge, so the
//  failure mode is the old behaviour. KNOWN LIMIT: a shortcut whose command only hands off to
//  another, already running app (Lutris/Heroic via flatpak, xdg-open) makes its reaper exit
//  within seconds while the real game keeps running; game-end could then fire early if ES-DE
//  is in front meanwhile. No such shortcut is launched from ES-DE on this Deck (2026-10-29).
//
//  Pure logic, no ES-DE/SDL/X11 dependencies: main.cpp feeds it one Sample per loop pass and
//  tests/test_background_game_end.cpp replays recorded timelines through it.
//

#ifndef ES_APP_BACKGROUND_GAME_END_WATCH_H
#define ES_APP_BACKGROUND_GAME_END_WATCH_H

#include <algorithm>
#include <cstdint>
#include <string>
#include <vector>

class BackgroundGameEndWatch
{
public:
    enum class Focus {
        UNKNOWN, // gamescope not available, identity not learned yet, or the poll failed
        FOCUSED, // ES-DE positively has gamescope input focus
        UNFOCUSED
    };

    enum class Event {
        NONE,
        ARMED, // a background launch is pending; baseline reapers recorded
        NO_BASELINE, // the reapers could not be listed before the launch: it is not watched
        SEEN, // the launched game's reaper appeared
        GONE, // every seen game reaper has exited
        FIRE, // fire game-end now (returned at most once per launch)
        TRAILED_GAME_GONE, // after a switch-back: the user went back to that game, it has now
                           // exited and ES-DE is in front again
        SEEN_TIMEOUT, // no game reaper appeared in time; the first-input path stays in charge
        DISARMED // the launch was handled by another path (e.g. the first input)
    };

    struct Sample {
        std::uint64_t nowMs {0}; // monotonic milliseconds (SDL_GetTicks64)
        bool launched {false}; // a background game-end is pending (launched state + params)
        unsigned int launchSerial {0}; // Window background-launch counter
        bool baselineOk {false}; // `baseline` was listed just before that launch's command
        std::vector<unsigned long> baseline; // reaper AppIds running just before the launch
        std::string launchRom; // the launched ROM (game-end param 1): recognises a relaunch
        Focus focus {Focus::UNKNOWN};
        bool uiIdle {true}; // nothing (menu, popup GUI) on top of the ViewController
        bool reapersScanned {false}; // true when `reapers` holds a fresh scan of this pass
        std::vector<unsigned long> reapers; // AppIds of the running Steam reapers
        unsigned long selfAppId {0}; // ES-DE's own appid when known (never the game)
    };

    // How long after arming a game reaper may take to appear (Steam can update a game first).
    static constexpr std::uint64_t kSeenDeadlineMs {30 * 60 * 1000};
    // How long "game gone + ES-DE in front + no menu" must hold before firing.
    static constexpr std::uint64_t kHoldMs {250};
    // How long "ES-DE in front + no menu" must hold while the game still runs (a switch-back).
    // Longer, so a brief hand-back of focus between windows (15-20 ms recorded) never runs the
    // cleanup. "Away" is set by Steam's UI taking focus at the start of every launch, so a return
    // to ES-DE lasting this long during Steam's launch phase fires too: the game-start setup is
    // then undone before the game shows, but the user is back in ES-DE and would otherwise sit
    // behind the launch splash, which drops their input. Not seen in any recording (2026-10-29).
    static constexpr std::uint64_t kSwitchBackHoldMs {1000};
    // A larger gap between two samples (e.g. ES-DE was SIGSTOPped by PauseGames) restarts the
    // hold: time ES-DE did not observe must not count as time the condition held.
    static constexpr std::uint64_t kMaxSampleGapMs {120};
    // Reaper scan cadence: fast while waiting to SEE the game (its reaper may only be visible
    // for a short while before PauseGames freezes ES-DE), slower once it is being tracked.
    static constexpr std::uint64_t kScanIntervalArmedMs {100};
    static constexpr std::uint64_t kScanIntervalSeenMs {250};
    // A fresh scan always lands at or before the pass that would FIRE for a quit, and any change
    // of the game's alive state restarts the hold, so one scan that misses a live reaper can
    // never fire on its own. Keep it that way.
    static_assert(kScanIntervalSeenMs <= kHoldMs, "a FIRE must be preceded by a fresh scan");

    // Should the caller scan the reapers for the next update()? Cheap, call every pass. A launch
    // is armed from the Sample's baseline without a scan, scanned at once on the next pass, then
    // at the state's cadence; TRAILING keeps scanning although its launch has been handled. A
    // launch not armed yet, or a finished watch, never scans.
    bool wantsReaperScan(bool launched, unsigned int launchSerial, std::uint64_t nowMs) const
    {
        const bool watching {launched && (mState == State::ARMED || mState == State::SEEN)};
        if (launchSerial != mSerial || (!watching && mState != State::TRAILING))
            return false;
        if (!mHasScanned)
            return true;
        const std::uint64_t interval {mState == State::ARMED ? kScanIntervalArmedMs :
                                                               kScanIntervalSeenMs};
        return nowMs - mLastScanMs >= interval;
    }

    Event update(const Sample& s)
    {
        // A switch-back handled the launch (FIRE or a first input; the launch is cleared) but its
        // game still runs: TRAILING follows it until it exits or the next launch comes along.
        const bool trailing {mState == State::TRAILING && s.launchSerial == mSerial};
        if (!s.launched && !trailing) {
            // The first input handled a launch whose game still runs (upstream's switch-back
            // game-end): follow the game, as after a switch-back FIRE.
            if (mState == State::SEEN && mGameAlive && s.launchSerial == mSerial &&
                s.focus == Focus::FOCUSED) {
                mState = State::TRAILING;
                return Event::DISARMED;
            }
            const bool wasWatching {mState == State::ARMED || mState == State::SEEN};
            reset();
            return wasWatching ? Event::DISARMED : Event::NONE;
        }

        // A new launch: arm it with the reapers listed just before its launch command. Each
        // launch is armed once; one whose list failed is left to the first-input path.
        if (s.launchSerial != mSerial) {
            // Relaunching the game a switch-back left running gives no new reaper, so its running
            // reapers are this launch's game. Only a fallback now: ViewController refuses to start
            // a Steam launch script whose game is still running.
            std::vector<unsigned long> carried;
            if (mState == State::TRAILING && s.launchRom == mRom) {
                for (const unsigned long app : mGame) {
                    if (contains(s.baseline, app))
                        carried.push_back(app);
                }
            }
            reset();
            mSerial = s.launchSerial;
            if (!s.baselineOk) {
                mState = State::DONE;
                return Event::NO_BASELINE;
            }
            mState = carried.empty() ? State::ARMED : State::SEEN;
            mBaseline = s.baseline;
            mRom = s.launchRom;
            mGame = carried;
            mGameAlive = !carried.empty();
            mArmedAtMs = s.nowMs;
            return Event::ARMED;
        }

        if (mState == State::IDLE || mState == State::DONE)
            return Event::NONE;

        if (s.reapersScanned) {
            mHasScanned = true;
            mLastScanMs = s.nowMs;
        }

        if (mState == State::TRAILING) {
            // Away (the game has the front): nothing seen before counts any more, and the exit
            // must be seen again once ES-DE is back in front.
            if (s.focus == Focus::UNFOCUSED) {
                mAwayWhileTrailing = true;
                mGoneSeenInFront = false;
            }
            if (s.reapersScanned) {
                const bool alive {anyGameAlive(s.reapers)};
                // The next scan without the game, after it was seen gone in front, ends this.
                if (!alive && mGoneSeenInFront) {
                    mState = State::DONE;
                    return Event::NONE;
                }
                if (alive)
                    mGoneSeenInFront = false;
                mGameAlive = alive;
            }
            // In front while the game still runs (switched back again): an exit seen from here
            // later is no return from the game.
            if (s.focus == Focus::FOCUSED && mGameAlive)
                mAwayWhileTrailing = false;
            if (mGameAlive || mGoneSeenInFront || s.focus != Focus::FOCUSED)
                return Event::NONE;
            mGoneSeenInFront = true;
            // Gone, ES-DE in front, and the user was in the game meanwhile: they are back from
            // it, so the caller briefly ignores A, as after a quit. An exit seen without having
            // left ES-DE (the game closed slowly or crashed, or one scan missed it) is no return.
            return mAwayWhileTrailing ? Event::TRAILED_GAME_GONE : Event::NONE;
        }

        // Another app (Steam's UI, the game) had the front since arming, so a later return to
        // ES-DE while the game still runs is a switch-back.
        if (s.focus == Focus::UNFOCUSED)
            mWasAway = true;

        Event event {Event::NONE};

        if (s.reapersScanned) {
            bool added {false};
            for (const unsigned long app : s.reapers) {
                if (app == 0 || app == kSteamUiAppId || app == s.selfAppId ||
                    contains(mBaseline, app) || contains(mGame, app))
                    continue;
                mGame.push_back(app);
                added = true;
            }
            if (added && mState == State::ARMED) {
                mState = State::SEEN;
                event = Event::SEEN;
            }
            if (mState == State::SEEN) {
                const bool alive {anyGameAlive(s.reapers)};
                if (mGameAlive && !alive && event == Event::NONE)
                    event = Event::GONE;
                // Restart the hold on any change: "gone" then needs this scan AND the next one
                // (see the static_assert), even during a switch-back hold already running.
                if (alive != mGameAlive)
                    mHolding = false;
                mGameAlive = alive;
            }
        }

        if (mState == State::ARMED) {
            if (s.nowMs - mArmedAtMs > kSeenDeadlineMs) {
                mState = State::DONE;
                return Event::SEEN_TIMEOUT;
            }
            return event;
        }

        // State::SEEN
        const bool ready {s.focus == Focus::FOCUSED && s.uiIdle && (!mGameAlive || mWasAway)};
        const bool gap {mHasLastSample && s.nowMs - mLastSampleMs > kMaxSampleGapMs};
        mHasLastSample = true;
        mLastSampleMs = s.nowMs;
        if (!ready || gap)
            mHolding = false;
        if (ready) {
            if (!mHolding) {
                mHolding = true;
                mHoldSinceMs = s.nowMs;
            }
            if (s.nowMs - mHoldSinceMs >= (mGameAlive ? kSwitchBackHoldMs : kHoldMs)) {
                mFiredWhileGameRunning = mGameAlive;
                // A switch-back leaves the game running: follow it to its exit.
                mState = mGameAlive ? State::TRAILING : State::DONE;
                return Event::FIRE;
            }
        }
        return event;
    }

    // For logging.
    const std::vector<unsigned long>& baseline() const { return mBaseline; }
    const std::vector<unsigned long>& gameAppIds() const { return mGame; }
    // After FIRE: true when it fired for a switch-back (the game was still running).
    bool firedWhileGameRunning() const { return mFiredWhileGameRunning; }
    // TRAILING: following a game whose launch is already handled. The launched state no longer
    // holds off the screensaver then, so the caller must keep its idle time from counting the
    // time that game has the front (main.cpp).
    bool followingGame() const { return mState == State::TRAILING; }

private:
    enum class State {
        IDLE,
        ARMED,
        SEEN,
        TRAILING, // after a switch-back: the launch is handled, its game still runs
        DONE
    };

    // Steam's own UI appid under gamescope; never the launched game.
    static constexpr unsigned long kSteamUiAppId {769};

    static bool contains(const std::vector<unsigned long>& v, unsigned long app)
    {
        return std::find(v.cbegin(), v.cend(), app) != v.cend();
    }

    bool anyGameAlive(const std::vector<unsigned long>& reapers) const
    {
        for (const unsigned long app : mGame) {
            if (contains(reapers, app))
                return true;
        }
        return false;
    }

    void reset()
    {
        mState = State::IDLE;
        mBaseline.clear();
        mRom.clear();
        mGame.clear();
        mGameAlive = false;
        mWasAway = false;
        mFiredWhileGameRunning = false;
        mAwayWhileTrailing = false;
        mGoneSeenInFront = false;
        mHolding = false;
        mHasLastSample = false;
        mHasScanned = false;
        // mSerial survives: it is the last launch armed, so no launch is ever armed twice.
    }

    State mState {State::IDLE};
    unsigned int mSerial {0};
    std::vector<unsigned long> mBaseline;
    std::string mRom;
    std::vector<unsigned long> mGame;
    bool mGameAlive {false};
    bool mWasAway {false};
    bool mFiredWhileGameRunning {false};
    bool mAwayWhileTrailing {false};
    bool mGoneSeenInFront {false};
    std::uint64_t mArmedAtMs {0};
    bool mHolding {false};
    std::uint64_t mHoldSinceMs {0};
    bool mHasLastSample {false};
    std::uint64_t mLastSampleMs {0};
    bool mHasScanned {false};
    std::uint64_t mLastScanMs {0};
};

#endif // ES_APP_BACKGROUND_GAME_END_WATCH_H
