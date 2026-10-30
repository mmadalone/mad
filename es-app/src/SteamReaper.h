//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  SteamReaper.h  (deck-patches)
//
//  Finds the games Steam is running: Steam starts every game (and every non-Steam shortcut,
//  ES-DE itself included) under its subreaper launcher
//      .../ubuntu12_32/reaper SteamLaunch AppId=<id> -- <command...>
//  which lives until every process of that game has exited. The PauseGames Decky plugin
//  identifies games the same way. Used by BackgroundGameEndWatch to tell when a Steam game
//  launched from ES-DE has really ended, and by ViewController::triggerGameLaunch() to not
//  start a Steam game that is still running.
//

#ifndef ES_APP_STEAM_REAPER_H
#define ES_APP_STEAM_REAPER_H

#include <string>
#include <vector>

namespace SteamReaper
{
    // Parse one /proc/<pid>/cmdline (NUL-separated argv). Returns true and sets appId when it
    // is Steam's `reaper SteamLaunch ... AppId=<n> ...` launcher. Only arguments before "--"
    // are considered: what follows is the game's own command line.
    inline bool parseCmdline(const std::string& cmdline, unsigned long& appId)
    {
        std::vector<std::string> argv;
        std::string::size_type start {0};
        while (start < cmdline.size()) {
            std::string::size_type end {cmdline.find('\0', start)};
            if (end == std::string::npos)
                end = cmdline.size();
            argv.emplace_back(cmdline.substr(start, end - start));
            start = end + 1;
        }
        if (argv.size() < 3)
            return false;

        const std::string& exe {argv[0]};
        const std::string::size_type slash {exe.rfind('/')};
        if ((slash == std::string::npos ? exe : exe.substr(slash + 1)) != "reaper")
            return false;
        if (argv[1] != "SteamLaunch")
            return false;

        for (std::size_t i {2}; i < argv.size(); ++i) {
            const std::string& arg {argv[i]};
            if (arg == "--")
                break;
            if (arg.compare(0, 6, "AppId=") != 0)
                continue;
            const std::string digits {arg.substr(6)};
            if (digits.empty() || digits.size() > 10 ||
                digits.find_first_not_of("0123456789") != std::string::npos)
                return false;
            const unsigned long long value {std::stoull(digits)};
            if (value == 0 || value > 0xFFFFFFFFULL)
                return false;
            appId = static_cast<unsigned long>(value);
            return true;
        }
        return false;
    }

    // Parse a Steam launch script (the steam system's ROM files are 3-4 line shell scripts that
    // run `exec steam steam://rungameid/<id>`). Returns true and sets appId to the AppId Steam's
    // reaper carries for that game. <id> is a Steam game id: bits 24-31 are its type. Type 0, a
    // Steam game: <id> is the AppId. Type 2, a non-Steam shortcut: the AppId is the high 32
    // bits, e.g. 13961374314801397760 = (3250635768 << 32) | 0x02000000. Other types (mods,
    // P2P) have no known reaper AppId and return false. Only the first URL counts.
    inline bool parseRungameid(const std::string& text, unsigned long& appId)
    {
        const std::string key {"steam://rungameid/"};
        const std::string::size_type at {text.find(key)};
        if (at == std::string::npos)
            return false;

        unsigned long long id {0};
        std::string::size_type pos {at + key.size()};
        const std::string::size_type first {pos};
        for (; pos < text.size() && text[pos] >= '0' && text[pos] <= '9'; ++pos) {
            const unsigned long long digit {static_cast<unsigned long long>(text[pos] - '0')};
            if (id > (0xFFFFFFFFFFFFFFFFULL - digit) / 10)
                return false; // More than 64 bits.
            id = id * 10 + digit;
        }
        if (pos == first)
            return false;

        const unsigned long long type {(id >> 24) & 0xFF};
        const unsigned long long high {id >> 32};
        unsigned long long value {0};
        if (type == 0 && high == 0)
            value = id;
        else if (type == 2)
            value = high;
        if (value == 0)
            return false;
        appId = static_cast<unsigned long>(value);
        return true;
    }

    // Fill `appIds` (deduplicated) with the AppIds of every running Steam reaper. Returns false
    // when the process list cannot be read; always false on non-Linux platforms.
    bool listAppIds(std::vector<unsigned long>& appIds);

    // True, setting appId, when `romPath` is a Steam launch script (a regular file of at most
    // 4 KB that parseRungameid() accepts) whose game Steam is running right now. False when it
    // isn't such a script, when the process list can't be read, and on non-Linux platforms.
    bool launchScriptRunningAppId(const std::string& romPath, unsigned long& appId);

} // namespace SteamReaper

#endif // ES_APP_STEAM_REAPER_H
