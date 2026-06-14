//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadWiiBridge.cpp
//
//  Lifetime owner of the MAD wii-nav-bridge daemon (deck-patches).
//

#include "guis/mad/MadWiiBridge.h"

#include "Log.h"
#include "utils/FileSystemUtil.h"

#include <csignal>
#include <cstring>
#include <fcntl.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

int MadWiiBridge::sPid {-1};
int MadWiiBridge::sStdinFd {-1};

namespace
{
    // Opportunistic reap (called from every pipe write + shutdown): nothing
    // else waitpids the bridge, so an early death would leave a zombie for
    // the whole session. SIGCHLD must stay default — launchGameUnix's
    // popen/system need their own waitpid semantics intact.
    void reapBridge(int& pid)
    {
        if (pid > 0 && waitpid(pid, nullptr, WNOHANG) == pid)
            pid = -1;
    }
} // namespace

void MadWiiBridge::spawn()
{
    if (sPid > 0)
        return;

    const std::string script {Utils::FileSystem::getHomePath() +
                              "/Emulation/tools/launchers/wii-nav-bridge.py"};
    if (!Utils::FileSystem::exists(script)) {
        LOG(LogInfo) << "MadWiiBridge: " << script << " not present — wii nav disabled";
        return;
    }
    // Writing to a dead bridge must error (EPIPE), not kill ES-DE. (Children
    // inherit the ignored disposition through exec; emulators handling EPIPE
    // instead of dying on SIGPIPE is the benign direction.)
    signal(SIGPIPE, SIG_IGN);
    const std::string logDir {Utils::FileSystem::getHomePath() +
                              "/Emulation/storage/controller-router"};
    Utils::FileSystem::createDirectory(logDir);

    int pipeFds[2];
    if (pipe(pipeFds) != 0) {
        LOG(LogError) << "MadWiiBridge: pipe() failed";
        return;
    }
    const pid_t pid {fork()};
    if (pid < 0) {
        close(pipeFds[0]);
        close(pipeFds[1]);
        LOG(LogError) << "MadWiiBridge: fork() failed";
        return;
    }
    if (pid == 0) {
        // Die with ES-DE: ask the kernel to SIGTERM this child when the parent
        // (thread that forked) goes away, so a crash/kill that skips the clean
        // stdin-EOF shutdown still reaps the bridge. Preserved across execve for a
        // non-setuid binary, so it survives into python3. Async-signal-safe.
        prctl(PR_SET_PDEATHSIG, SIGTERM);
        // Child: stdin = our pipe; stdout → /dev/null; stderr → the log.
        dup2(pipeFds[0], STDIN_FILENO);
        close(pipeFds[0]);
        close(pipeFds[1]);
        const int devNull {open("/dev/null", O_WRONLY)};
        if (devNull >= 0)
            dup2(devNull, STDOUT_FILENO);
        const int logFd {open((logDir + "/wii-nav-bridge.log").c_str(),
                              O_WRONLY | O_CREAT | O_APPEND, 0644)};
        if (logFd >= 0)
            dup2(logFd, STDERR_FILENO);
        execlp("python3", "python3", script.c_str(), static_cast<char*>(nullptr));
        _exit(127);
    }
    close(pipeFds[0]);
    sPid = static_cast<int>(pid);
    sStdinFd = pipeFds[1];
    // CLOEXEC: launched games must not inherit the write end — a background
    // game outliving ES-DE would otherwise hold the pipe open and the bridge
    // would never see EOF (PDEATHSIG would be its only lifeline).
    fcntl(sStdinFd, F_SETFD, FD_CLOEXEC);
    LOG(LogInfo) << "MadWiiBridge: spawned (pid " << sPid << ")";
}

void MadWiiBridge::writeLine(const char* line)
{
    if (sStdinFd < 0)
        return;
    const ssize_t written {write(sStdinFd, line, strlen(line))};
    if (written < 0) {
        LOG(LogWarning) << "MadWiiBridge: bridge pipe dead (" << strerror(errno) << ")";
        close(sStdinFd);
        sStdinFd = -1;
        reapBridge(sPid); // No zombie; sPid resets so spawn() could relaunch.
        sPid = -1;
    }
}

void MadWiiBridge::pause()
{
    LOG(LogDebug) << "MadWiiBridge: pause";
    writeLine("pause\n");
}

void MadWiiBridge::resume()
{
    LOG(LogDebug) << "MadWiiBridge: resume";
    writeLine("resume\n");
}

void MadWiiBridge::shutdown()
{
    if (sStdinFd >= 0) {
        close(sStdinFd); // EOF — the bridge exits; PDEATHSIG is the backup.
        sStdinFd = -1;
    }
    if (sPid > 0) {
        // Give it a moment to exit on EOF, then reap (non-blocking retries).
        for (int i {0}; i < 10 && waitpid(sPid, nullptr, WNOHANG) == 0; ++i)
            usleep(50000);
        sPid = -1;
    }
}
