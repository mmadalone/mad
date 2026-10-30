//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  SteamReaper.cpp  (deck-patches)
//
//  See SteamReaper.h. A full scan reads /proc/<pid>/cmdline for every process (~330 on a
//  Steam Deck in Game Mode, about a millisecond); BackgroundGameEndWatch only asks for one
//  while a background launch is pending, at most every 100 ms.
//

#include "SteamReaper.h"

#if defined(__linux__) && !defined(__ANDROID__)
#include <algorithm>
#include <cctype>
#include <cerrno>
#include <dirent.h>
#include <fstream>
#include <iterator>
#endif

namespace SteamReaper
{
    bool listAppIds(std::vector<unsigned long>& appIds)
    {
        appIds.clear();
#if defined(__linux__) && !defined(__ANDROID__)
        DIR* proc {opendir("/proc")};
        if (proc == nullptr)
            return false;

        while (const dirent* entry {readdir(proc)}) {
            const char* name {entry->d_name};
            if (name[0] == '\0' || !std::isdigit(static_cast<unsigned char>(name[0])))
                continue;
            // Processes come and go during the scan; an unreadable one is simply skipped.
            errno = 0;
            std::ifstream file {std::string("/proc/") + name + "/cmdline", std::ios::binary};
            if (!file) {
                // Gone mid-scan is normal; anything else (e.g. out of file descriptors) means
                // this scan can't be trusted to say a game has exited.
                // EACCES/EPERM: another user's entry under hidepid; the game's reaper runs as
                // this user, so skipping those is safe.
                if (errno != 0 && errno != ENOENT && errno != ESRCH && errno != EACCES &&
                    errno != EPERM) {
                    closedir(proc);
                    appIds.clear();
                    return false;
                }
                continue;
            }
            std::string cmdline;
            cmdline.resize(4096);
            file.read(&cmdline[0], static_cast<std::streamsize>(cmdline.size()));
            cmdline.resize(static_cast<std::size_t>(file.gcount()));
            // Cheap pre-filter before splitting argv.
            if (cmdline.find("SteamLaunch") == std::string::npos)
                continue;
            unsigned long appId {0};
            if (parseCmdline(cmdline, appId) &&
                std::find(appIds.cbegin(), appIds.cend(), appId) == appIds.cend())
                appIds.emplace_back(appId);
        }
        closedir(proc);
        return true;
#else
        return false;
#endif
    }

} // namespace SteamReaper
