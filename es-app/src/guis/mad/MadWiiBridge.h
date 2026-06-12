//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadWiiBridge.h
//
//  Lifetime owner of the MAD wii-nav-bridge daemon (deck-patches): Wii
//  Remotes on a mode-4 DolphinBar navigate ES-DE/MAD through the bridge's
//  virtual uinput pad ("MAD Wii Nav"). Spawned once at startup; paused
//  around every game launch (the bridge must NEVER write to the DolphinBar
//  slots while a game owns the remotes — Dolphin real-Wiimote); dies with
//  ES-DE (stdin EOF + PR_SET_PDEATHSIG on the Python side).
//

#ifndef ES_APP_GUIS_MAD_MAD_WII_BRIDGE_H
#define ES_APP_GUIS_MAD_MAD_WII_BRIDGE_H

class MadWiiBridge
{
public:
    static void spawn(); // Idempotent; failure is non-fatal (no bridge = no wii nav).
    static void pause(); // Before a game launch: slots released, controls zeroed.
    static void resume(); // After the game returns.
    static void shutdown(); // Close the pipe (EOF) — the bridge exits cleanly.

private:
    static void writeLine(const char* line);

    static int sPid;
    static int sStdinFd;
};

#endif // ES_APP_GUIS_MAD_MAD_WII_BRIDGE_H
