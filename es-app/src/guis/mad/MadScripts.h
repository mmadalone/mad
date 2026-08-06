//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadScripts.h
//
//  deck-patches. The script-revision contract between this binary and the MAD
//  launcher scripts.
//
//  MAD ships in two halves that update independently: this AppImage reaches a
//  Deck automatically through ES-DE's in-app updater, while the scripts on the
//  `main` branch arrive only when someone runs git pull / install.sh by hand.
//  Nothing enforced that they match, and at CI run 50 the panel shipped features
//  whose backend methods were not deployed, so they simply errored on every Deck
//  with older scripts, with nothing on screen to explain why.
//
//  Two hand-authored integers, because the two branches share no counter:
//
//    MAD_SCRIPTS_MIN_REV   (here)              what this binary NEEDS
//    SCRIPTS_REV           (tracked, `main`)   what is deployed
//
//  BUMP ORDER IS LOAD-BEARING: raise SCRIPTS_REV on main and push it FIRST, then
//  raise MAD_SCRIPTS_MIN_REV here. build-appimage.yml refuses to publish a build
//  whose minimum exceeds what main can deliver, so getting it backwards fails in
//  CI rather than on a device.
//
//  Bump when the panel starts calling a backend method unconditionally, or needs
//  a hook/launcher that older scripts lack. Do NOT bump for cosmetic C++ changes:
//  every bump costs the user an install.sh run, and nag fatigue is how a real
//  warning gets ignored.
//
//  Deliberately NOT the launchers' VERSION file, which is a human release marker
//  bumped a handful of times in the repo's life. This must move on a completely
//  different cadence, and conflating them guarantees one of the two stops meaning
//  anything.

#ifndef ES_APP_GUIS_MAD_MAD_SCRIPTS_H
#define ES_APP_GUIS_MAD_MAD_SCRIPTS_H

#include "utils/FileSystemUtil.h"

#include <fstream>
#include <string>

// The script revision this build requires. See the bump rules above.
// 1 = the contract itself; no build has demanded anything beyond it yet.
constexpr int MAD_SCRIPTS_MIN_REV {1};

namespace MadScripts
{
    static inline std::string launchersDir()
    {
        return Utils::FileSystem::getHomePath() + "/Emulation/tools/launchers";
    }

    // First whitespace-separated token as a non-negative int, or -1 for "no answer"
    // (absent, unreadable, empty, non-numeric). -1 is distinct from 0 on purpose:
    // 0 means "genuinely ancient", -1 means "do not claim anything".
    static inline int readInt(const std::string& path)
    {
        std::ifstream file {path};
        if (!file.good())
            return -1;
        std::string token;
        if (!(file >> token))
            return -1;
        try {
            const int value {std::stoi(token)};
            return value >= 0 ? value : -1;
        }
        catch (const std::exception&) {
            return -1; // non-numeric or out of range: a corrupt file is not evidence
        }
    }

    // OUR OWN state lives in the ES-DE app data dir, NEVER in the launchers dir.
    //
    // The launchers dir is a git CLONE, and install.sh refuses to pull when
    // `git status --porcelain` is non-empty - untracked files included. Writing binary-only
    // state in there means dismissing this very dialog would dirty the clone and block
    // install.sh, which is the remedy the dialog prescribes: the skew could then never be
    // fixed, and the snooze would stop it ever warning again. Only .esde-scripts-expect
    // belongs over there, because the scripts half reads it at that exact path.
    static inline std::string stateDir() { return Utils::FileSystem::getAppDataDirectory(); }

    // What the scripts say they are. A checkout predating SCRIPTS_REV genuinely IS
    // older than any build demanding one, so absent counts as 0 rather than unknown.
    //
    // .scripts-rev-test forces a value so the dialog can be exercised on a healthy Deck
    // without downgrading anything (mirrors .healthcheck-test). It lives in the app data
    // dir for the reason above - a test affordance must not brick the fix path.
    static inline int deployedRev()
    {
        const int forced {readInt(stateDir() + "/.scripts-rev-test")};
        if (forced >= 0)
            return forced;
        const int rev {readInt(launchersDir() + "/SCRIPTS_REV")};
        return rev < 0 ? 0 : rev;
    }

    // Publish what this build needs, so the scripts can compare without knowing anything
    // about the binary. This one file MUST live in the launchers dir (lib/version_skew.py
    // reads it there), so the write is IDEMPOTENT: re-writing an identical value on every
    // start would keep a pre-contract checkout permanently dirty, and install.sh would
    // refuse to pull the very update that fixes it.
    static inline void publishExpectedRev()
    {
        const std::string dir {launchersDir()};
        if (!Utils::FileSystem::isDirectory(dir))
            return; // no launchers install: nothing to talk to
        const std::string path {dir + "/.esde-scripts-expect"};
        if (readInt(path) == MAD_SCRIPTS_MIN_REV)
            return; // already correct - do not touch the file
        std::ofstream file {path};
        if (file.good())
            file << MAD_SCRIPTS_MIN_REV << "\n";
    }

    // The same "nothing to talk to" guard publishExpectedRev uses. Without it a Deck with
    // no launchers checkout reads 0, decides it is behind, and gets an un-dismissable
    // startup box pointing at an install.sh that does not exist on that machine.
    static inline bool scriptsAreBehind()
    {
        return Utils::FileSystem::isDirectory(launchersDir()) &&
               deployedRev() < MAD_SCRIPTS_MIN_REV;
    }

    // Snooze is keyed on the STATE, never a bare bool. A boolean "dismissed" flag is
    // how the post-update flag ended up with an unreachable clear path; here a new
    // build or any script change makes the stored key stop matching, so the warning
    // re-arms by itself and cannot be silenced into the very bug it exists to catch.
    // In the app data dir, not the launchers clone: see stateDir(). That directory is
    // created on first start and always exists, so DON'T REMIND actually sticks.
    static inline std::string snoozePath() { return stateDir() + "/.scripts-skew-snooze"; }

    static inline std::string snoozeKey()
    {
        return std::to_string(deployedRev()) + ":" + std::to_string(MAD_SCRIPTS_MIN_REV);
    }

    static inline bool snoozed()
    {
        std::ifstream file {snoozePath()};
        if (!file.good())
            return false;
        std::string stored;
        std::getline(file, stored);
        return stored == snoozeKey();
    }

    static inline void snooze()
    {
        std::ofstream file {snoozePath()};
        if (file.good())
            file << snoozeKey() << "\n";
    }
} // namespace MadScripts

#endif // ES_APP_GUIS_MAD_MAD_SCRIPTS_H
