//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadBackend.h
//
//  Process supervisor and JSON-RPC client for mad-backend.py (deck-patches).
//  Spawns the Python daemon with pipes on stdin/stdout and speaks the NDJSON
//  protocol documented in deck-docs/mad-backend-protocol.md (launchers repo).
//

#ifndef ES_APP_GUIS_MAD_MAD_BACKEND_H
#define ES_APP_GUIS_MAD_MAD_BACKEND_H

#include "guis/mad/MadJson.h"

#include <atomic>
#include <chrono>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <signal.h>
#include <sys/types.h>

// Protocol version this panel speaks. Bump only on breaking wire changes, in
// lock-step with PROTO in mad-backend.py.
constexpr int MAD_PROTO_EXPECTED {1};

class MadBackend
{
public:
    enum class State {
        Spawning,
        WaitingHello,
        Ready,
        Errored
    };

    // For ok=false the payload is the error object: {"code": "...", "message": "..."}.
    using ResponseCallback = std::function<void(bool ok, const rapidjson::Value& payload)>;
    using EventCallback = std::function<void(const rapidjson::Value& data)>;

    MadBackend();
    ~MadBackend();

    void spawn();
    // Drains the reader queue and dispatches callbacks. Call every frame from the
    // panel's update(), UI thread only.
    void poll();
    void request(const std::string& method,
                 const MadJson::ParamsWriter& params,
                 const ResponseCallback& callback,
                 const int timeoutMs = 4000);
    // Best-effort shutdown request + stdin EOF (the daemon's primary teardown
    // signal) + SIGTERM. Pending callbacks are dropped silently.
    void terminate();
    // Manual retry (RETRY button): resets the auto-restart budget and respawns.
    void restart();

    State state() const { return mState; }
    const std::string& errorMessage() const { return mErrorMessage; }
    void setOnReady(const std::function<void()>& callback) { mOnReady = callback; }
    void setEventCallback(const std::string& event, const EventCallback& callback);
    void setStreamCallback(const std::string& token, const EventCallback& callback);
    void clearStreamCallback(const std::string& token);

private:
    struct PendingRequest {
        ResponseCallback callback;
        std::chrono::steady_clock::time_point deadline;
    };

    void readerLoop(const int fd);
    void enqueue(std::unique_ptr<rapidjson::Document> doc);
    void dispatchMessage(const rapidjson::Document& doc);
    void enterErrored(const std::string& message);
    void failAllPending(const char* code, const char* message);
    void completeWithError(const ResponseCallback& callback, const char* code, const char* message);
    void handleChildDeath();
    // Closes stdin (optionally requesting shutdown first), SIGTERMs, reaps with a
    // ~2 second grace period before SIGKILL, joins the reader and closes the fds.
    // Always clears mStreamCallbacks; on the death path (requestShutdown false)
    // it first synthesizes a {closed:true} delivery via closeAllStreams().
    void shutdownChild(const bool requestShutdown);
    // Delivers a synthesized {"closed":true} to every registered stream callback
    // and clears the map (collect-then-call: a callback may delete its owner).
    void closeAllStreams();
    bool writeLine(const std::string& line);

    State mState;
    std::string mErrorMessage;
    std::function<void()> mOnReady;

    pid_t mChildPid;
    int mStdinFd;
    int mStdoutFd;

    std::thread mReaderThread;
    std::atomic<bool> mDead;
    std::atomic<bool> mReaderDone;

    std::mutex mQueueMutex;
    std::deque<std::unique_ptr<rapidjson::Document>> mQueue;

    // Delivers stashed pushes for tokens whose callback has been registered
    // since they arrived. UI thread only (called from poll()).
    void deliverUnclaimedStreams();

    int mNextId;
    std::map<int, PendingRequest> mPending;
    std::map<std::string, EventCallback> mEventCallbacks;
    std::map<std::string, EventCallback> mStreamCallbacks;
    // Stream pushes that arrived before setStreamCallback() registered their
    // token: a stream's worker thread may emit (e.g. capture's instant
    // {"error": "no gamepads connected"}) before the method response carrying
    // the token has been dispatched. Kept until the callback shows up, capped
    // per token, cleared on shutdown. UI thread only.
    std::map<std::string, std::vector<std::unique_ptr<rapidjson::Document>>> mUnclaimedStreams;

    std::vector<std::chrono::steady_clock::time_point> mRestartTimes;
    std::chrono::steady_clock::time_point mHelloDeadline;

    // SIGPIPE disposition from before the first spawn(); restored by the
    // destructor so launched games don't inherit a process-global SIG_IGN.
    struct sigaction mPrevSigpipe;
    bool mSigpipeSaved;
};

#endif // ES_APP_GUIS_MAD_MAD_BACKEND_H
