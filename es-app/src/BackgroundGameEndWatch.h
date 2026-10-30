//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  BackgroundGameEndWatch.h  (deck-patches)
//
//  Decides WHEN a game launched with %RUNINBACKGROUND% (the Steam system) has really ended, so
//  the main loop can fire its game-end event without waiting for the user's first input.
//
//  Upstream fires that game-end "on application wakeup" (INSTALL.md), i.e. on the first input
//  after the user comes back. On the Steam Deck that meant: the launch splash lingering, the
//  first press eaten (the A filter) or frozen for seconds while the game-end scripts ran, and
//  the cleanup hooks (cloud resume/push, power, controller router) not running at all until a
//  button was pressed - one recorded run fired 7 minutes after the game had exited.
//
//  The signals (recorded on the Deck 2026-10-29, see deck-docs/gamescope-focus.md):
//    * game alive  = Steam's launcher `reaper SteamLaunch AppId=<id> -- ...` for that game is
//      running. The reaper lives until every process of the game has exited (Steam's own
//      "Running" state); the PauseGames Decky plugin finds games the same way. It starts while
//      ES-DE is still running (Steam's launch phase), before PauseGames freezes ES-DE.
//    * ES-DE in front = GamescopeFocus's STRICT verdict (Unknown when it cannot tell).
//
//  Rule:  ARMED at a successful background launch (baseline = reapers running then)
//         -> SEEN when a reaper AppId outside the baseline appears (within kSeenDeadlineMs)
//         -> FIRE once all seen reapers have exited AND ES-DE is positively in front AND no
//            menu is open, continuously for kHoldMs of OBSERVED time.
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
        SEEN, // the launched game's reaper appeared
        GONE, // every seen game reaper has exited
        FIRE, // fire game-end now (returned at most once per launch)
        SEEN_TIMEOUT, // no game reaper appeared in time; the first-input path stays in charge
        DISARMED // the launch was handled by another path (e.g. the first input)
    };

    struct Sample {
        std::uint64_t nowMs {0}; // monotonic milliseconds (SDL_GetTicks64)
        bool launched {false}; // a background game-end is pending (launched state + params)
        unsigned int launchSerial {0}; // Window background-launch counter
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
    // A larger gap between two samples (e.g. ES-DE was SIGSTOPped by PauseGames) restarts the
    // hold: time ES-DE did not observe must not count as time the condition held.
    static constexpr std::uint64_t kMaxSampleGapMs {120};
    // Reaper scan cadence: fast while waiting to SEE the game (its reaper may only be visible
    // for a short while before PauseGames freezes ES-DE), slower once it is being tracked.
    static constexpr std::uint64_t kScanIntervalArmedMs {100};
    static constexpr std::uint64_t kScanIntervalSeenMs {250};
    // A fresh scan always lands at or before the pass that would FIRE, so one scan that misses
    // a live reaper can never fire on its own. Keep it that way.
    static_assert(kScanIntervalSeenMs <= kHoldMs, "a FIRE must be preceded by a fresh scan");

    // Should the caller scan the reapers for the next update()? Cheap, call every pass. A new
    // launch gets its baseline scan at once; a finished watch never scans.
    bool wantsReaperScan(bool launched, unsigned int launchSerial, std::uint64_t nowMs) const
    {
        if (!launched)
            return false;
        if (mState == State::IDLE || launchSerial != mSerial || !mHasScanned)
            return true;
        if (mState == State::DONE)
            return false;
        const std::uint64_t interval {mState == State::SEEN ? kScanIntervalSeenMs :
                                                              kScanIntervalArmedMs};
        return nowMs - mLastScanMs >= interval;
    }

    Event update(const Sample& s)
    {
        if (!s.launched) {
            const bool wasWatching {mState == State::ARMED || mState == State::SEEN};
            reset();
            return wasWatching ? Event::DISARMED : Event::NONE;
        }

        if (s.reapersScanned) {
            mHasScanned = true;
            mLastScanMs = s.nowMs;
        }

        // A new launch (or the first pass of one): take the baseline from a fresh scan.
        if (mState == State::IDLE || s.launchSerial != mSerial) {
            if (!s.reapersScanned)
                return Event::NONE;
            reset();
            mHasScanned = true;
            mLastScanMs = s.nowMs;
            mSerial = s.launchSerial;
            mState = State::ARMED;
            mBaseline = s.reapers;
            mArmedAtMs = s.nowMs;
            return Event::ARMED;
        }

        if (mState == State::DONE)
            return Event::NONE;

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
                bool alive {false};
                for (const unsigned long app : mGame)
                    alive = alive || contains(s.reapers, app);
                if (mGameAlive && !alive && event == Event::NONE)
                    event = Event::GONE;
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
        const bool ready {!mGameAlive && s.focus == Focus::FOCUSED && s.uiIdle};
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
            if (s.nowMs - mHoldSinceMs >= kHoldMs) {
                mState = State::DONE;
                return Event::FIRE;
            }
        }
        return event;
    }

    // For logging.
    const std::vector<unsigned long>& baseline() const { return mBaseline; }
    const std::vector<unsigned long>& gameAppIds() const { return mGame; }

private:
    enum class State {
        IDLE,
        ARMED,
        SEEN,
        DONE
    };

    // Steam's own UI appid under gamescope; never the launched game.
    static constexpr unsigned long kSteamUiAppId {769};

    static bool contains(const std::vector<unsigned long>& v, unsigned long app)
    {
        return std::find(v.cbegin(), v.cend(), app) != v.cend();
    }

    void reset()
    {
        mState = State::IDLE;
        mBaseline.clear();
        mGame.clear();
        mGameAlive = false;
        mHolding = false;
        mHasLastSample = false;
        // mHasScanned/mLastScanMs/mSerial survive: they describe the caller's cadence and the
        // last launch handled, not the per-launch watch state.
    }

    State mState {State::IDLE};
    unsigned int mSerial {0};
    std::vector<unsigned long> mBaseline;
    std::vector<unsigned long> mGame;
    bool mGameAlive {false};
    std::uint64_t mArmedAtMs {0};
    bool mHolding {false};
    std::uint64_t mHoldSinceMs {0};
    bool mHasLastSample {false};
    std::uint64_t mLastSampleMs {0};
    bool mHasScanned {false};
    std::uint64_t mLastScanMs {0};
};

#endif // ES_APP_BACKGROUND_GAME_END_WATCH_H
