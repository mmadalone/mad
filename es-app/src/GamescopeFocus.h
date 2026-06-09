//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GamescopeFocus.h
//
//  Steam Deck / gamescope native "PauseGames": detects when ES-DE has lost INPUT focus
//  (the Steam overlay/QAM is up over it, or it's been backgrounded) by polling gamescope's
//  root-window atom GAMESCOPE_FOCUSED_APP and comparing it to ES-DE's own appid. This lets
//  the main loop block input (and pause previews / skip rendering) while unfocused, removing
//  the need for the external SDH-PauseGames Decky plugin. Self-disables when not running
//  under gamescope, so it is a no-op on the desktop and on non-Linux platforms.
//
//  Note: with gamescope's --xwayland-count 2 (the Steam Deck Game Mode setup) the atoms live
//  on the PRIMARY X server (":0", shared with Steam) while ES-DE renders to a nested display
//  ($DISPLAY, often ":1"), so init() probes for the display that actually exposes the atom.
//

#ifndef ES_APP_GAMESCOPE_FOCUS_H
#define ES_APP_GAMESCOPE_FOCUS_H

#include <string>

class GamescopeFocus
{
public:
    // Idempotent. Finds the X display carrying the gamescope focus atoms and interns them;
    // if none is reachable (no X server / not gamescope) the feature stays disabled forever.
    void init();

    // True if ES-DE currently has gamescope input focus (or the feature is disabled).
    // Internally throttled to ~60 Hz, so it is cheap to call every frame.
    bool hasFocus();

    // Low-volume diagnostic log to ~/Emulation/storage/controller-router/gamescope-focus.log
    // (init result, learned appid, focus transitions, guide-button events). Safe to leave on.
    static void debugLog(const std::string& msg);

private:
    // Opaque X11 handles (kept as void*/unsigned long so Xlib stays out of the header).
    void* mDisplay {nullptr};
    unsigned long mRoot {0};
    unsigned long mAtomFocusApp {0};
    unsigned long mAtomFocusGfx {0};
    unsigned long mAtomFocusWindow {0};
    unsigned long mAtomKbdDisplay {0};
    unsigned long mMyAppId {0};      // self-learned: rendered appid once ES-DE is foreground
    unsigned long mMyWindow {0};     // self-learned: ES-DE's focused window when foreground
    unsigned long mMyKbdDisplay {0}; // self-learned: the keyboard-focus display when foreground

    unsigned int mFirstPollMs {0};
    unsigned int mLastPollMs {0};
    bool mInitialized {false};
    bool mEnabled {false};
    bool mHasFocus {true};
};

#endif // ES_APP_GAMESCOPE_FOCUS_H
