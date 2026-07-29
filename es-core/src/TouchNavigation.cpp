//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  TouchNavigation.cpp
//
//  Tap-the-UI touchscreen navigation (deck-patches TOUCH). See TouchNavigation.h.
//

#include "TouchNavigation.h"

#include "InputManager.h"
#include "Log.h"
#include "Settings.h"
#include "Window.h"
#include "renderers/Renderer.h"

#include <cmath>

namespace
{
    // Movement below this distance (from the press position) still counts as a tap.
    // 1.5% of the shorter screen dimension is 12 px on a 1280x800 Deck, roughly the
    // 8 dp Android touch slop with some padding for handheld shake.
    constexpr float TAP_SLOP_FACTOR {0.015f};
    constexpr float TAP_SLOP_MINIMUM_PIXELS {8.0f};

    // Two-finger tap: both fingers must release within this time of the second
    // finger going down for the gesture to count as "back".
    constexpr Uint32 TWO_FINGER_TAP_MAX_MS {250};

    // An active gesture with no events for this long is considered stale (its
    // finger-up was probably eaten by a pause) and is dropped.
    constexpr Uint32 STALE_GESTURE_MS {10000};

    // Fling (kinetic scroll): minimum release velocity to start, velocity half-life
    // while decaying, and the velocity below which the fling stops.
    constexpr float FLING_START_VELOCITY {350.0f}; // Pixels per second.
    constexpr float FLING_HALF_LIFE_SECONDS {0.35f};
    constexpr float FLING_STOP_VELOCITY {60.0f};

    // Velocity is sampled over windows of at least this length.
    constexpr Uint32 VELOCITY_SAMPLE_MS {50};
} // namespace

TouchNavigation& TouchNavigation::getInstance()
{
    static TouchNavigation instance;
    return instance;
}

TouchNavigation::TouchNavigation() noexcept
    : mState {State::IDLE}
    , mMousePointer {false}
    , mMouseButtonDown {false}
    , mFingerId {0}
    , mSecondFingerActive {false}
    , mHadSecondFinger {false}
    , mSecondFingerId {0}
    , mSecondFingerDownTime {0}
    , mDownPosition {0.0f, 0.0f}
    , mLastPosition {0.0f, 0.0f}
    , mLastEventTime {0}
    , mVelocitySamplePosition {0.0f, 0.0f}
    , mVelocitySampleTime {0}
    , mFlingActive {false}
    , mFlingStartPoint {0.0f, 0.0f}
    , mFlingVelocity {0.0f, 0.0f}
    , mFlingTopGui {nullptr}
{
}

bool TouchNavigation::touchNavigationEnabled() const
{
    return Settings::getInstance()->getBool("InputTouchNavigation");
}

void TouchNavigation::logDebug(const std::string& message) const
{
    if (Settings::getInstance()->getBool("DebugTouchNavigation")) {
        LOG(LogInfo) << "TouchNavigation: " << message;
    }
}

glm::vec2 TouchNavigation::windowToUiCoords(const glm::vec2& windowPosition) const
{
    const float screenWidth {Renderer::getScreenWidth()};
    const float screenHeight {Renderer::getScreenHeight()};

    // The rendered viewport can sit offset inside the window (ScreenOffsetX/Y,
    // FullscreenPadding); map to viewport-local coordinates first. Zero on a
    // default Steam Deck setup.
    const glm::ivec2 viewportOrigin {Renderer::getInstance()->getViewportOrigin()};
    const glm::vec2 viewportPosition {windowPosition.x - static_cast<float>(viewportOrigin.x),
                                      windowPosition.y - static_cast<float>(viewportOrigin.y)};

    switch (Renderer::getInstance()->getScreenRotation()) {
        case 90: {
            return glm::vec2 {viewportPosition.y, screenHeight - viewportPosition.x};
        }
        case 180: {
            return glm::vec2 {screenWidth - viewportPosition.x, screenHeight - viewportPosition.y};
        }
        case 270: {
            return glm::vec2 {screenWidth - viewportPosition.y, viewportPosition.x};
        }
        default: {
            return viewportPosition;
        }
    }
}

bool TouchNavigation::parseFingerEvent(const SDL_Event& event)
{
    if (!touchNavigationEnabled())
        return false;

    int windowWidth {0};
    int windowHeight {0};
    SDL_GetWindowSize(Renderer::getInstance()->getSDLWindow(), &windowWidth, &windowHeight);
    const glm::vec2 position {windowToUiCoords(
        glm::vec2 {event.tfinger.x * static_cast<float>(windowWidth),
                   event.tfinger.y * static_cast<float>(windowHeight)})};

    switch (event.type) {
        case SDL_FINGERDOWN: {
            // An active gesture with no events for a long time lost its finger-up to
            // a pause (game launch, Steam overlay): drop it and start fresh.
            if (mState != State::IDLE &&
                event.tfinger.timestamp - mLastEventTime > STALE_GESTURE_MS) {
                logDebug("dropping stale gesture");
                reset();
            }
            if (mState == State::IDLE) {
                mMousePointer = false;
                mFingerId = event.tfinger.fingerId;
                pointerDown(position, event.tfinger.timestamp);
            }
            else if (!mMousePointer && event.tfinger.fingerId != mFingerId) {
                secondFingerDown(event.tfinger.fingerId, event.tfinger.timestamp);
            }
            return true;
        }
        case SDL_FINGERMOTION: {
            if (!mMousePointer && mState != State::IDLE && event.tfinger.fingerId == mFingerId)
                pointerMove(position, event.tfinger.timestamp);
            return true;
        }
        case SDL_FINGERUP: {
            if (!mMousePointer && mState != State::IDLE && event.tfinger.fingerId == mFingerId)
                pointerUp(position, event.tfinger.timestamp);
            else if (mSecondFingerActive && event.tfinger.fingerId == mSecondFingerId)
                secondFingerUp(event.tfinger.timestamp);
            return true;
        }
        default: {
            return false;
        }
    }
}

bool TouchNavigation::parseMouseEvent(const SDL_Event& event)
{
    if (!touchNavigationEnabled())
        return false;

    switch (event.type) {
        case SDL_MOUSEBUTTONDOWN: {
            // Touches already arrive as finger events; SDL's synthesized duplicates
            // must be dropped or every tap would dispatch twice.
            if (event.button.which == SDL_TOUCH_MOUSEID || event.button.button != SDL_BUTTON_LEFT)
                return false;
            // Same stale-gesture recovery as the finger path.
            if (mState != State::IDLE &&
                event.button.timestamp - mLastEventTime > STALE_GESTURE_MS) {
                logDebug("dropping stale gesture");
                reset();
            }
            if (mState == State::IDLE) {
                mMousePointer = true;
                mMouseButtonDown = true;
                pointerDown(windowToUiCoords(glm::vec2 {static_cast<float>(event.button.x),
                                                        static_cast<float>(event.button.y)}),
                            event.button.timestamp);
            }
            return true;
        }
        case SDL_MOUSEMOTION: {
            if (event.motion.which == SDL_TOUCH_MOUSEID)
                return false;
            if (mMousePointer && mMouseButtonDown && mState != State::IDLE) {
                pointerMove(windowToUiCoords(glm::vec2 {static_cast<float>(event.motion.x),
                                                        static_cast<float>(event.motion.y)}),
                            event.motion.timestamp);
                return true;
            }
            return false;
        }
        case SDL_MOUSEBUTTONUP: {
            if (event.button.which == SDL_TOUCH_MOUSEID || event.button.button != SDL_BUTTON_LEFT)
                return false;
            if (mMousePointer && mState != State::IDLE) {
                mMouseButtonDown = false;
                pointerUp(windowToUiCoords(glm::vec2 {static_cast<float>(event.button.x),
                                                      static_cast<float>(event.button.y)}),
                          event.button.timestamp);
                return true;
            }
            return false;
        }
        default: {
            return false;
        }
    }
}

void TouchNavigation::pointerDown(const glm::vec2& position, const Uint32 timestamp)
{
    // A new press always stops a running fling (the standard touch-to-stop gesture).
    cancelFling();

    if (mState != State::IDLE)
        return;

    // Window handles the press-time interceptions (input blocked, screensaver wake,
    // launch screen dismissal). When it consumes the press the rest of the gesture
    // is swallowed so the wake-tap can't also activate something underneath.
    if (Window::getInstance()->pointerPress(position)) {
        mState = State::SWALLOWED;
        logDebug("press swallowed by Window");
    }
    else {
        mState = State::PENDING;
    }

    mDownPosition = position;
    mLastPosition = position;
    mLastEventTime = timestamp;
    mVelocitySamplePosition = position;
    mVelocitySampleTime = timestamp;
    mSecondFingerActive = false;
    mHadSecondFinger = false;

    if (Settings::getInstance()->getBool("DebugTouchNavigation")) {
        LOG(LogInfo) << "TouchNavigation: " << (mMousePointer ? "mouse" : "finger")
                     << " down at (" << position.x << ", " << position.y << ")";
    }
}

void TouchNavigation::pointerMove(const glm::vec2& position, const Uint32 timestamp)
{
    if (mState == State::SWALLOWED) {
        mLastPosition = position;
        mLastEventTime = timestamp;
        return;
    }

    const glm::vec2 delta {position - mLastPosition};

    if (mState == State::PENDING) {
        const float slop {std::max(
            TAP_SLOP_MINIMUM_PIXELS,
            TAP_SLOP_FACTOR * std::min(Renderer::getScreenWidth(), Renderer::getScreenHeight()))};
        if (glm::length(position - mDownPosition) >= slop) {
            mState = State::SCROLLING;
            // The first SCROLL event carries the whole distance since the press so
            // no movement is lost to the slop.
            Window::getInstance()->pointerScroll(mDownPosition, position,
                                                 position - mDownPosition, true);
            logDebug("drag started");
        }
    }
    else if (mState == State::SCROLLING) {
        Window::getInstance()->pointerScroll(mDownPosition, position, delta, false);
    }

    // Roll the velocity sample window (used by pointerUp for fling detection).
    if (timestamp - mVelocitySampleTime >= VELOCITY_SAMPLE_MS) {
        mVelocitySamplePosition = position;
        mVelocitySampleTime = timestamp;
    }

    mLastPosition = position;
    mLastEventTime = timestamp;
}

void TouchNavigation::pointerUp(const glm::vec2& position, const Uint32 timestamp)
{
    const State endedState {mState};
    const bool twoFingerTap {mSecondFingerActive && mState == State::PENDING &&
                             timestamp - mSecondFingerDownTime <= TWO_FINGER_TAP_MAX_MS};
    const bool hadSecondFinger {mHadSecondFinger};

    mState = State::IDLE;
    mSecondFingerActive = false;
    mHadSecondFinger = false;

    if (twoFingerTap) {
        logDebug("two-finger tap, synthesizing b");
        synthesizeInput("b");
        return;
    }

    if (endedState == State::PENDING) {
        // A missed two-finger tap (second finger participated but released too
        // slowly) is swallowed: an intended "back" must never turn into a tap.
        if (hadSecondFinger) {
            logDebug("late two-finger gesture swallowed");
            return;
        }
        logDebug("tap dispatched");
        Window::getInstance()->pointerTap(mDownPosition);
        return;
    }

    if (endedState == State::SCROLLING) {
        // Release velocity from the FINAL sample window: a finger that stopped and
        // held before lifting produces a stale (old) sample, which must not fling
        // the list away from the row the user carefully stopped on.
        glm::vec2 releaseVelocity {0.0f, 0.0f};
        const Uint32 sampleAge {timestamp - mVelocitySampleTime};
        if (sampleAge > 0 && sampleAge <= 4 * VELOCITY_SAMPLE_MS)
            releaseVelocity = (position - mVelocitySamplePosition) *
                              (1000.0f / static_cast<float>(sampleAge));

        // Start a fling when the pointer was released at speed. The fling keeps
        // dispatching SCROLL events hit-tested at the gesture's start point, so it
        // stays with the surface the drag started on.
        if (glm::length(releaseVelocity) >= FLING_START_VELOCITY &&
            !Window::getInstance()->isMediaViewerActive() &&
            !Window::getInstance()->isPDFViewerActive() &&
            Window::getInstance()->getGuiStackSize() > 0) {
            mFlingActive = true;
            mFlingStartPoint = mDownPosition;
            mFlingVelocity = releaseVelocity;
            mFlingTopGui = Window::getInstance()->peekGui();
            logDebug("fling started");
        }
    }
}

void TouchNavigation::secondFingerDown(const SDL_FingerID fingerId, const Uint32 timestamp)
{
    // Only meaningful while the primary finger is still within the tap slop; extra
    // fingers during a drag, and third fingers, are ignored.
    if (mState == State::PENDING && !mSecondFingerActive) {
        mSecondFingerActive = true;
        mHadSecondFinger = true;
        mSecondFingerId = fingerId;
        mSecondFingerDownTime = timestamp;
    }
}

void TouchNavigation::secondFingerUp(const Uint32 timestamp)
{
    if (mState == State::PENDING && mSecondFingerActive &&
        timestamp - mSecondFingerDownTime <= TWO_FINGER_TAP_MAX_MS) {
        mState = State::SWALLOWED;
        mSecondFingerActive = false;
        logDebug("two-finger tap, synthesizing b");
        synthesizeInput("b");
        return;
    }
    mSecondFingerActive = false;
}

void TouchNavigation::cancelFling()
{
    mFlingActive = false;
    mFlingVelocity = {0.0f, 0.0f};
    mFlingTopGui = nullptr;
}

void TouchNavigation::update(const int deltaTime)
{
    if (!mFlingActive)
        return;

    Window* window {Window::getInstance()};

    // The GUI identity check uses pointer comparison only (never dereferenced): any
    // change to the top of the stack cancels the fling.
    if (!touchNavigationEnabled() || window->getBlockInput() ||
        window->getGuiStackSize() == 0 || window->peekGui() != mFlingTopGui) {
        cancelFling();
        return;
    }

    const float seconds {static_cast<float>(deltaTime) / 1000.0f};
    const glm::vec2 delta {mFlingVelocity * seconds};
    mFlingVelocity *= std::pow(0.5f, seconds / FLING_HALF_LIFE_SECONDS);

    window->pointerScroll(mFlingStartPoint, mFlingStartPoint, delta, false, true);

    if (glm::length(mFlingVelocity) < FLING_STOP_VELOCITY)
        cancelFling();
}

void TouchNavigation::reset()
{
    mState = State::IDLE;
    mMouseButtonDown = false;
    mSecondFingerActive = false;
    mHadSecondFinger = false;
    cancelFling();
}

std::string TouchNavigation::promptToConfigName(const std::string& prompt)
{
    if (prompt == "a" || prompt == "b" || prompt == "x" || prompt == "y" || prompt == "start" ||
        prompt == "back")
        return prompt;
    else if (prompt == "l")
        return "leftshoulder";
    else if (prompt == "r")
        return "rightshoulder";
    else if (prompt == "lt")
        return "lefttrigger";
    else if (prompt == "rt")
        return "righttrigger";
    else
        return "";
}

void TouchNavigation::synthesizeInput(const std::string& name)
{
    InputConfig* config {InputManager::getInstance().getInputConfigByDevice(DEVICE_TOUCH)};
    if (config == nullptr)
        return;

    Input mapped;
    if (!config->getInputByName(name, &mapped))
        return;

    if (Settings::getInstance()->getBool("DebugTouchNavigation")) {
        LOG(LogInfo) << "TouchNavigation: synthesizing input \"" << name << "\" (id "
                     << mapped.id << ")";
    }

    Window* window {Window::getInstance()};
    window->input(config, Input(DEVICE_TOUCH, TYPE_TOUCH, mapped.id, 1, false));
    // Always release so hold-to-scroll repeat logic can never wedge on.
    window->input(config, Input(DEVICE_TOUCH, TYPE_TOUCH, mapped.id, 0, false));
}
