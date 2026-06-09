//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GamescopeFocus.cpp
//
//  See GamescopeFocus.h. The X11 polling is compiled in only on Linux (non-Android); on
//  every other platform init() does nothing and hasFocus() always returns true. debugLog()
//  is portable (it just appends to a file under $HOME).
//

#include "GamescopeFocus.h"

#include <cstdlib>
#include <fstream>

void GamescopeFocus::debugLog(const std::string& msg)
{
    const char* home {std::getenv("HOME")};
    if (home == nullptr)
        return;
    std::ofstream out {std::string(home) +
                           "/Emulation/storage/controller-router/gamescope-focus.log",
                       std::ios::app};
    if (out)
        out << msg << "\n";
}

#if defined(__linux__) && !defined(__ANDROID__)
#include <SDL2/SDL_timer.h>
#include <X11/Xatom.h>
#include <X11/Xlib.h>

namespace
{
    // Read a single 32-bit CARDINAL property from a window. Xlib returns format-32 data as an
    // array of long, so the value is the first long (the gamescope appids fit in 32 bits).
    bool readCardinal(Display* display, Window window, Atom atom, unsigned long& out)
    {
        Atom actualType {0};
        int actualFormat {0};
        unsigned long nItems {0};
        unsigned long bytesAfter {0};
        unsigned char* prop {nullptr};

        if (XGetWindowProperty(display, window, atom, 0, 1, False, XA_CARDINAL, &actualType,
                               &actualFormat, &nItems, &bytesAfter, &prop) != Success)
            return false;

        bool ok {false};
        if (prop != nullptr && actualType == XA_CARDINAL && actualFormat == 32 && nItems >= 1) {
            // Xlib stores format-32 props in (signed) long slots; mask to the real 32-bit
            // CARDINAL so a high-bit appid (e.g. 0xFF050011) isn't sign-extended.
            out = *reinterpret_cast<unsigned long*>(prop) & 0xFFFFFFFFUL;
            ok = true;
        }
        if (prop != nullptr)
            XFree(prop);
        return ok;
    }

    // Open the named display (nullptr = $DISPLAY) and return it only if its root window
    // actually carries a readable GAMESCOPE_FOCUSED_APP property. This selects by atom
    // presence rather than a hardcoded display number, so it ignores unrelated X servers
    // (e.g. a VNC display in Desktop Mode). Otherwise close and return nullptr.
    Display* tryDisplay(const char* name)
    {
        Display* display {XOpenDisplay(name)};
        if (display == nullptr)
            return nullptr;
        Atom app {XInternAtom(display, "GAMESCOPE_FOCUSED_APP", True)};
        unsigned long value {0};
        if (app != None && readCardinal(display, DefaultRootWindow(display), app, value))
            return display;
        XCloseDisplay(display);
        return nullptr;
    }
} // namespace
#endif

void GamescopeFocus::init()
{
    if (mInitialized)
        return;
    mInitialized = true;

#if defined(__linux__) && !defined(__ANDROID__)
    // Under gamescope (--xwayland-count 2 on the Steam Deck) the GAMESCOPE_FOCUSED_APP atoms
    // live on the PRIMARY X server (":0", shared with Steam/the overlay), NOT on the nested
    // display ES-DE itself renders to ($DISPLAY, often ":1"). So probe $DISPLAY first, then
    // ":0".."3", and use whichever root actually exposes the atom.
    const char* candidates[] {std::getenv("DISPLAY"), ":0", ":1", ":2", ":3"};
    Display* display {nullptr};
    std::string chosen;
    for (const char* name : candidates) {
        if (name == nullptr || name[0] == '\0')
            continue;
        display = tryDisplay(name);
        if (display != nullptr) {
            chosen = name;
            break;
        }
    }
    if (display == nullptr) {
        debugLog("init: no display exposes GAMESCOPE_FOCUSED_APP -> native pause DISABLED");
        return; // Not under gamescope (or atom unreachable) -> stay disabled.
    }

    mDisplay = static_cast<void*>(display);
    mRoot = static_cast<unsigned long>(DefaultRootWindow(display));
    mAtomFocusApp = static_cast<unsigned long>(XInternAtom(display, "GAMESCOPE_FOCUSED_APP", True));
    mAtomFocusGfx =
        static_cast<unsigned long>(XInternAtom(display, "GAMESCOPE_FOCUSED_APP_GFX", True));
    mAtomFocusWindow =
        static_cast<unsigned long>(XInternAtom(display, "GAMESCOPE_FOCUSED_WINDOW", True));
    mAtomKbdDisplay =
        static_cast<unsigned long>(XInternAtom(display, "GAMESCOPE_KEYBOARD_FOCUS_DISPLAY", True));
    mEnabled = (mAtomFocusApp != None && mAtomFocusGfx != None && mAtomFocusWindow != None &&
                mAtomKbdDisplay != None);
    debugLog("init: using display " + chosen +
             (mEnabled ? " -> native pause ENABLED" : " -> atoms missing, DISABLED"));
#endif
}

bool GamescopeFocus::hasFocus()
{
#if defined(__linux__) && !defined(__ANDROID__)
    if (!mEnabled)
        return true;

    const unsigned int now {SDL_GetTicks()};
    if (mFirstPollMs == 0)
        mFirstPollMs = now;
    // Throttle to ~60 Hz (one X round-trip per frame at most); cached verdict in between. Kept
    // tight so the pause engages within a frame of the overlay opening (e.g. on Guide+X).
    if (mLastPollMs != 0 && now - mLastPollMs < 16)
        return mHasFocus;
    mLastPollMs = now;

    Display* display {static_cast<Display*>(mDisplay)};
    unsigned long focusedApp {0};
    unsigned long focusedGfx {0};
    unsigned long focusedWindow {0};
    unsigned long kbdDisplay {0};

    // If the input-focus atom can't be read, keep the previous verdict (fail toward "focused").
    if (!readCardinal(display, static_cast<Window>(mRoot), static_cast<Atom>(mAtomFocusApp),
                      focusedApp))
        return mHasFocus;
    readCardinal(display, static_cast<Window>(mRoot), static_cast<Atom>(mAtomFocusGfx),
                 focusedGfx);
    readCardinal(display, static_cast<Window>(mRoot), static_cast<Atom>(mAtomFocusWindow),
                 focusedWindow);
    readCardinal(display, static_cast<Window>(mRoot), static_cast<Atom>(mAtomKbdDisplay),
                 kbdDisplay);

    // Self-learn our identity once ES-DE is TRULY foreground -- the focused app equals the
    // rendered app (focusedApp == focusedGfx), i.e. no overlay is redirecting input focus. Wait
    // ~2 s so we don't latch onto the launcher before ES-DE has taken over.
    if (mMyAppId == 0 && focusedGfx != 0 && focusedApp == focusedGfx &&
        now - mFirstPollMs > 2000) {
        mMyAppId = focusedGfx;
        mMyWindow = focusedWindow;
        mMyKbdDisplay = kbdDisplay;
        debugLog("learned app " + std::to_string(mMyAppId) + " win " +
                 std::to_string(mMyWindow) + " kbd " + std::to_string(mMyKbdDisplay));
    }

    // ES-DE has input focus only when KEYBOARD_FOCUS_DISPLAY is still ours: that is what the
    // Steam overlay/QAM steals even while GAMESCOPE_FOCUSED_APP keeps reading ES-DE (it points
    // at our app thumbnail). Require the focused app to match too. Until learned, never block.
    const bool previous {mHasFocus};
    mHasFocus = (mMyAppId == 0) ? true
                                : (focusedApp == mMyAppId && kbdDisplay == mMyKbdDisplay);
    if (mHasFocus != previous)
        debugLog(std::string(mHasFocus ? "FOCUS gained" : "FOCUS lost") + " (app=" +
                 std::to_string(focusedApp) + " win=" + std::to_string(focusedWindow) + " kbd=" +
                 std::to_string(kbdDisplay) + " me=" + std::to_string(mMyAppId) + "/" +
                 std::to_string(mMyWindow) + "/" + std::to_string(mMyKbdDisplay) + ")");
    return mHasFocus;
#else
    return true;
#endif
}
