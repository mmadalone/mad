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
//  launched from ES-DE has really ended.
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

    // Fill `appIds` (deduplicated) with the AppIds of every running Steam reaper. Returns false
    // when the process list cannot be read; always false on non-Linux platforms.
    bool listAppIds(std::vector<unsigned long>& appIds);

} // namespace SteamReaper

#endif // ES_APP_STEAM_REAPER_H
