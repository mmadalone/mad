//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadBackend.cpp
//
//  Process supervisor and JSON-RPC client for mad-backend.py (deck-patches).
//

#include "guis/mad/MadBackend.h"

#include "Log.h"
#include "utils/FileSystemUtil.h"

#include <cerrno>
#include <csignal>
#include <cstring>

#include <fcntl.h>
#include <sys/wait.h>
#include <unistd.h>

namespace
{
    // Events are coalesced/capped; responses (messages carrying an "id") never are.
    constexpr size_t MAX_QUEUED_EVENTS {256};
    constexpr int HELLO_TIMEOUT_SECONDS {10};
} // namespace

MadBackend::MadBackend()
    : mState {State::Spawning}
    , mChildPid {-1}
    , mStdinFd {-1}
    , mStdoutFd {-1}
    , mDead {false}
    , mReaderDone {false}
    , mNextId {0}
    , mPrevSigpipe {}
    , mSigpipeSaved {false}
{
}

MadBackend::~MadBackend()
{
    terminate();
    // Restore the pre-panel SIGPIPE disposition: while the backend is alive it
    // must stay ignored (writeLine() relies on EPIPE instead of a signal), but
    // a process-global SIG_IGN would be inherited by every game launched later.
    if (mSigpipeSaved)
        sigaction(SIGPIPE, &mPrevSigpipe, nullptr);
}

void MadBackend::spawn()
{
    mState = State::Spawning;
    mErrorMessage.clear();
    mDead = false;
    mReaderDone = false;

    // A write to a dead child's pipe must surface as EPIPE, not kill ES-DE.
    // Capture the previous disposition once (the first spawn; restarts would
    // otherwise capture our own SIG_IGN) so the destructor can restore it.
    if (!mSigpipeSaved) {
        struct sigaction ignoreAction {};
        ignoreAction.sa_handler = SIG_IGN;
        sigemptyset(&ignoreAction.sa_mask);
        if (sigaction(SIGPIPE, &ignoreAction, &mPrevSigpipe) == 0)
            mSigpipeSaved = true;
    }

    const std::string home {Utils::FileSystem::getHomePath()};
    const std::string logDir {home + "/Emulation/storage/controller-router"};
    Utils::FileSystem::createDirectory(logDir);
    const std::string logPath {logDir + "/mad-backend.log"};
    const std::string script {home + "/Emulation/tools/launchers/mad-backend.py"};

    int inPipe[2] {-1, -1}; // Panel → daemon stdin.
    int outPipe[2] {-1, -1}; // Daemon stdout → panel.

    if (pipe2(inPipe, O_CLOEXEC) != 0) {
        enterErrored("Couldn't create pipes for the MAD backend: " +
                     std::string {strerror(errno)});
        return;
    }
    if (pipe2(outPipe, O_CLOEXEC) != 0) {
        close(inPipe[0]);
        close(inPipe[1]);
        enterErrored("Couldn't create pipes for the MAD backend: " +
                     std::string {strerror(errno)});
        return;
    }

    const pid_t pid {fork()};

    if (pid == -1) {
        close(inPipe[0]);
        close(inPipe[1]);
        close(outPipe[0]);
        close(outPipe[1]);
        enterErrored("Couldn't fork the MAD backend process: " + std::string {strerror(errno)});
        return;
    }

    if (pid == 0) {
        // Child: only async-signal-safe calls until exec. dup2() clears close-on-exec
        // so the child keeps exactly stdin/stdout/stderr.
        dup2(inPipe[0], STDIN_FILENO);
        dup2(outPipe[1], STDOUT_FILENO);
        const int logFd {open(logPath.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0644)};
        if (logFd != -1)
            dup2(logFd, STDERR_FILENO);
        execlp("python3", "python3", script.c_str(), static_cast<char*>(nullptr));
        _exit(127);
    }

    close(inPipe[0]);
    close(outPipe[1]);

    mChildPid = pid;
    mStdinFd = inPipe[1];
    mStdoutFd = outPipe[0];
    mState = State::WaitingHello;
    mHelloDeadline = std::chrono::steady_clock::now() + std::chrono::seconds(HELLO_TIMEOUT_SECONDS);

    const int readFd {mStdoutFd};
    mReaderThread = std::thread {[this, readFd] { readerLoop(readFd); }};

    LOG(LogInfo) << "MadBackend: Spawned mad-backend.py with PID " << pid;
}

void MadBackend::readerLoop(const int fd)
{
    // Never read the pipe from the UI thread: this thread keeps draining so the
    // daemon never blocks on a full pipe, and parses each line off the UI thread.
    std::string buffer;
    char chunk[4096];

    while (true) {
        const ssize_t count {read(fd, chunk, sizeof(chunk))};
        if (count == -1 && errno == EINTR)
            continue;
        if (count <= 0)
            break;
        buffer.append(chunk, static_cast<size_t>(count));

        size_t pos {0};
        size_t newline {std::string::npos};
        while ((newline = buffer.find('\n', pos)) != std::string::npos) {
            const std::string line {buffer.substr(pos, newline - pos)};
            pos = newline + 1;
            if (line.empty())
                continue;
            auto doc = std::make_unique<rapidjson::Document>();
            if (!MadJson::parseLine(line, *doc)) {
                LOG(LogWarning) << "MadBackend: Discarding unparseable line from the backend";
                continue;
            }
            enqueue(std::move(doc));
        }
        buffer.erase(0, pos);
    }

    mDead = true;
    mReaderDone = true;
}

void MadBackend::enqueue(std::unique_ptr<rapidjson::Document> doc)
{
    const bool isResponse {doc->HasMember("id")};
    std::unique_lock<std::mutex> lock {mQueueMutex};

    if (!isResponse) {
        // devices.watch snapshots are idempotent: keep only the latest queued push
        // per token. ONLY snapshot-shaped pushes (data carries "changed") may
        // coalesce — the capture stream's terminal result is followed within ~1ms
        // by {closed:true} on the SAME token, and both can sit in the queue
        // between two polls; replacing the result with the close would silently
        // no-op every identify/detect. Everything else queues in order.
        if (MadJson::getString(*doc, "event") == "stream") {
            const rapidjson::Value& data {MadJson::getMember(*doc, "data")};
            if (data.IsObject() && data.HasMember("changed")) {
                const std::string token {MadJson::getString(*doc, "stream")};
                for (auto& queued : mQueue) {
                    if (!queued->HasMember("id") &&
                        MadJson::getString(*queued, "event") == "stream" &&
                        MadJson::getString(*queued, "stream") == token) {
                        const rapidjson::Value& queuedData {
                            MadJson::getMember(*queued, "data")};
                        if (queuedData.IsObject() && queuedData.HasMember("changed")) {
                            queued = std::move(doc);
                            return;
                        }
                    }
                }
            }
        }
        // Cap queued events by dropping the oldest event. Never drop responses.
        size_t eventCount {0};
        for (auto& queued : mQueue) {
            if (!queued->HasMember("id"))
                ++eventCount;
        }
        if (eventCount >= MAX_QUEUED_EVENTS) {
            for (auto it = mQueue.begin(); it != mQueue.end(); ++it) {
                if (!(*it)->HasMember("id")) {
                    mQueue.erase(it);
                    break;
                }
            }
        }
    }

    mQueue.emplace_back(std::move(doc));
}

void MadBackend::poll()
{
    std::vector<std::unique_ptr<rapidjson::Document>> messages;
    {
        std::unique_lock<std::mutex> lock {mQueueMutex};
        while (!mQueue.empty()) {
            messages.emplace_back(std::move(mQueue.front()));
            mQueue.pop_front();
        }
    }
    for (auto& doc : messages)
        dispatchMessage(*doc);

    // A push stashed during this very loop (e.g. it arrived in the same batch
    // as, but ahead of, the response that carries its token) is delivered the
    // moment the response callback has registered the token — same frame.
    deliverUnclaimedStreams();

    const auto now {std::chrono::steady_clock::now()};

    // Synthesize ETIMEOUT for requests past their deadline.
    for (auto it = mPending.begin(); it != mPending.end();) {
        if (now >= it->second.deadline) {
            const ResponseCallback callback {it->second.callback};
            it = mPending.erase(it);
            completeWithError(callback, "ETIMEOUT", "Request to the MAD backend timed out");
        }
        else {
            ++it;
        }
    }

    if (mChildPid == -1)
        return;

    bool childDied {mDead.load()};

    if (!childDied && mState == State::WaitingHello && now >= mHelloDeadline) {
        LOG(LogError) << "MadBackend: No hello event within the startup deadline";
        childDied = true;
    }

    if (!childDied) {
        int status {0};
        if (waitpid(mChildPid, &status, WNOHANG) == mChildPid)
            childDied = true;
    }

    if (childDied)
        handleChildDeath();
}

void MadBackend::dispatchMessage(const rapidjson::Document& doc)
{
    if (doc.HasMember("id")) {
        const int id {MadJson::getInt(doc, "id", -1)};
        const auto it = mPending.find(id);
        if (it == mPending.end())
            return; // Late response to an expired request.
        const ResponseCallback callback {it->second.callback};
        mPending.erase(it);
        const bool ok {MadJson::getBool(doc, "ok", false)};
        if (callback)
            callback(ok, ok ? MadJson::getMember(doc, "result") : MadJson::getMember(doc, "error"));
        return;
    }

    const std::string event {MadJson::getString(doc, "event")};
    const rapidjson::Value& data {MadJson::getMember(doc, "data")};

    if (event == "hello") {
        const int proto {MadJson::getInt(data, "proto", -1)};
        if (proto != MAD_PROTO_EXPECTED) {
            shutdownChild(false);
            mRestartTimes.assign(2, std::chrono::steady_clock::now()); // No point retrying.
            enterErrored("MAD backend protocol mismatch: the backend speaks v" +
                         std::to_string(proto) + " but this panel expects v" +
                         std::to_string(MAD_PROTO_EXPECTED) +
                         " — update via deck-fetch-esde.sh / git pull on the launchers repo");
            return;
        }
        request(
            "hello.ack",
            [](MadJson::Writer& writer) {
                writer.Key("proto");
                writer.Int(MAD_PROTO_EXPECTED);
            },
            nullptr);
        mState = State::Ready;
        LOG(LogInfo) << "MadBackend: Handshake complete, backend version "
                     << MadJson::getString(data, "backend_version", "unknown");
        if (mOnReady)
            mOnReady();
        return;
    }

    if (event == "fatal") {
        shutdownChild(false);
        mRestartTimes.assign(2, std::chrono::steady_clock::now()); // Fatal means fatal.
        std::string message {
            MadJson::getString(data, "message", "The MAD backend reported a fatal error")};
        const std::string code {MadJson::getString(data, "code")};
        if (code == "ENODEPS")
            message.append(" — run deck-post-update.sh to reinstall the dependencies");
        else if (!code.empty())
            message.append(" (").append(code).append(")");
        enterErrored(message);
        return;
    }

    if (event == "stream") {
        const std::string token {MadJson::getString(doc, "stream")};
        const auto it = mStreamCallbacks.find(token);
        if (it != mStreamCallbacks.end() && it->second) {
            // Copy before invoking: the callback may erase its own entry (the
            // capture modal's finish() → clearStreamCallback) which would
            // invalidate the map reference mid-call.
            const EventCallback callback {it->second};
            callback(data);
        }
        else {
            // A terminal push with no subscriber is dead: no subscriber will
            // ever come for a closed stream, so stashing it would leak the
            // copied document for the whole session (every B-cancelled capture).
            if (MadJson::getBool(data, "closed", false))
                return;
            // The stream's thread emitted before the response that names the
            // token was dispatched — stash the push for deliverUnclaimedStreams().
            auto& pending = mUnclaimedStreams[token];
            if (pending.size() < 8) {
                auto copy = std::make_unique<rapidjson::Document>();
                copy->CopyFrom(doc, copy->GetAllocator());
                pending.emplace_back(std::move(copy));
            }
        }
        return;
    }

    const auto it = mEventCallbacks.find(event);
    if (it != mEventCallbacks.end() && it->second) {
        // Copy before invoking: the callback may (re)register callbacks and
        // invalidate the map reference mid-call.
        const EventCallback callback {it->second};
        callback(data);
    }
}

void MadBackend::deliverUnclaimedStreams()
{
    for (auto it = mUnclaimedStreams.begin(); it != mUnclaimedStreams.end();) {
        const auto callbackIt = mStreamCallbacks.find(it->first);
        if (callbackIt == mStreamCallbacks.end() || !callbackIt->second) {
            ++it;
            continue;
        }
        // Move the batch out first: the callback may unsubscribe or push more.
        std::vector<std::unique_ptr<rapidjson::Document>> batch {std::move(it->second)};
        it = mUnclaimedStreams.erase(it);
        for (auto& doc : batch)
            dispatchMessage(*doc);
    }
}

void MadBackend::request(const std::string& method,
                         const MadJson::ParamsWriter& params,
                         const ResponseCallback& callback,
                         const int timeoutMs)
{
    if (mStdinFd == -1) {
        completeWithError(callback, "EBACKEND_DIED", "The MAD backend is not running");
        return;
    }

    const int id {mNextId++};
    if (callback) {
        mPending[id] = PendingRequest {
            callback, std::chrono::steady_clock::now() + std::chrono::milliseconds(timeoutMs)};
    }

    if (!writeLine(MadJson::makeRequest(id, method, params))) {
        mPending.erase(id);
        // Write failure (EPIPE) means the backend is gone; the next poll() runs the
        // death/restart path for everything else that's pending.
        mDead = true;
        completeWithError(callback, "EBACKEND_DIED", "The MAD backend process died");
    }
}

bool MadBackend::writeLine(const std::string& line)
{
    std::string payload {line};
    payload.append("\n");

    size_t written {0};
    while (written < payload.length()) {
        const ssize_t count {
            write(mStdinFd, payload.data() + written, payload.length() - written)};
        if (count == -1 && errno == EINTR)
            continue;
        if (count <= 0)
            return false;
        written += static_cast<size_t>(count);
    }
    return true;
}

void MadBackend::terminate()
{
    // Intentional teardown: drop pending callbacks silently.
    mPending.clear();
    shutdownChild(true);
}

void MadBackend::restart()
{
    terminate();
    mRestartTimes.clear();
    spawn();
}

void MadBackend::shutdownChild(const bool requestShutdown)
{
    if (mStdinFd != -1) {
        if (requestShutdown && !mDead)
            writeLine(MadJson::makeRequest(mNextId++, "shutdown"));
        // Closing stdin (EOF) is the daemon's primary teardown signal.
        close(mStdinFd);
        mStdinFd = -1;
    }

    if (mChildPid != -1) {
        kill(mChildPid, SIGTERM);
        // Reap, escalating to SIGKILL after roughly two seconds.
        int status {0};
        bool reaped {false};
        for (int i {0}; i < 40; ++i) {
            const pid_t result {waitpid(mChildPid, &status, WNOHANG)};
            if (result == mChildPid || result == -1) {
                reaped = true;
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        if (!reaped) {
            LOG(LogWarning) << "MadBackend: Backend ignored SIGTERM, sending SIGKILL";
            kill(mChildPid, SIGKILL);
            waitpid(mChildPid, &status, 0);
        }
        mChildPid = -1;
    }

    // The child's death closes its stdout end, so the reader sees EOF and exits.
    // Give it up to two seconds before the (then immediate) join.
    if (mReaderThread.joinable()) {
        for (int i {0}; i < 40 && !mReaderDone; ++i)
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        if (!mReaderDone) {
            LOG(LogWarning) << "MadBackend: Reader thread slow to exit, joining anyway";
        }
        mReaderThread.join();
    }

    if (mStdoutFd != -1) {
        close(mStdoutFd);
        mStdoutFd = -1;
    }

    mDead = false;
    mReaderDone = false;
    mUnclaimedStreams.clear();

    {
        std::unique_lock<std::mutex> lock {mQueueMutex};
        mQueue.clear();
    }

    // Death path only: the daemon closes every stream on its own exit paths,
    // but those pushes died with it — a capture modal mid-stream would hang on
    // the armed prompt forever (its request was already answered, so
    // failAllPending can't reach it, and the daemon-side 15s timeout is gone).
    // Synthesize the {closed:true} delivery before clearing the subscribers.
    if (!requestShutdown)
        closeAllStreams();
    // Always drop the subscribers: the new daemon's token counter restarts at
    // s1, so a stale subscriber could swallow a fresh stream after a restart.
    mStreamCallbacks.clear();
}

void MadBackend::closeAllStreams()
{
    if (mStreamCallbacks.empty())
        return;

    // Collect the callbacks and clear the map BEFORE invoking anything: a
    // callback may unsubscribe or delete its owner during invocation (the
    // capture modal's finish() does `delete this`). Each copied std::function
    // keeps its own captures alive, so invoking the remaining copies is safe.
    std::vector<EventCallback> callbacks;
    callbacks.reserve(mStreamCallbacks.size());
    for (const auto& entry : mStreamCallbacks) {
        if (entry.second)
            callbacks.emplace_back(entry.second);
    }
    mStreamCallbacks.clear();

    rapidjson::Document closed;
    closed.SetObject();
    closed.AddMember("closed", true, closed.GetAllocator());
    for (const EventCallback& callback : callbacks)
        callback(closed);
}

void MadBackend::handleChildDeath()
{
    LOG(LogError) << "MadBackend: Backend process died unexpectedly";
    shutdownChild(false);
    failAllPending("EBACKEND_DIED", "The MAD backend process died");

    if (mState == State::Errored)
        return; // A fatal/proto-mismatch error screen is already up.

    // Auto-restart, at most twice within sixty seconds.
    const auto now {std::chrono::steady_clock::now()};
    while (!mRestartTimes.empty() && now - mRestartTimes.front() > std::chrono::seconds(60))
        mRestartTimes.erase(mRestartTimes.begin());

    if (mRestartTimes.size() >= 2) {
        enterErrored("The MAD backend keeps crashing — check "
                     "~/Emulation/storage/controller-router/mad-backend.log");
        return;
    }

    mRestartTimes.emplace_back(now);
    LOG(LogInfo) << "MadBackend: Restarting the backend (attempt " << mRestartTimes.size()
                 << " of 2)";
    spawn();
}

void MadBackend::enterErrored(const std::string& message)
{
    LOG(LogError) << "MadBackend: " << message;
    mState = State::Errored;
    mErrorMessage = message;
}

void MadBackend::failAllPending(const char* code, const char* message)
{
    std::map<int, PendingRequest> pending;
    pending.swap(mPending);
    for (auto& entry : pending)
        completeWithError(entry.second.callback, code, message);
}

void MadBackend::completeWithError(const ResponseCallback& callback,
                                   const char* code,
                                   const char* message)
{
    if (!callback)
        return;
    rapidjson::Document error;
    error.SetObject();
    error.AddMember("code", rapidjson::Value {code, error.GetAllocator()}, error.GetAllocator());
    error.AddMember("message", rapidjson::Value {message, error.GetAllocator()},
                    error.GetAllocator());
    callback(false, error);
}

void MadBackend::setEventCallback(const std::string& event, const EventCallback& callback)
{
    mEventCallbacks[event] = callback;
}

void MadBackend::setStreamCallback(const std::string& token, const EventCallback& callback)
{
    mStreamCallbacks[token] = callback;
}

void MadBackend::clearStreamCallback(const std::string& token)
{
    mStreamCallbacks.erase(token);
    mUnclaimedStreams.erase(token);
}
