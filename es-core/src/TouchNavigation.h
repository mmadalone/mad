//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  TouchNavigation.h
//
//  Tap-the-UI touchscreen navigation (deck-patches TOUCH). Consumes SDL finger
//  events (primary) and real-mouse events (Desktop Mode convenience) when the
//  "InputTouchNavigation" setting is enabled, recognizes taps, drags, flings and
//  two-finger taps, and routes them to Window::pointerTap()/pointerScroll() or
//  synthesizes standard controller Inputs on the DEVICE_TOUCH input config.
//
//  Design rule: touch never invents new behavior. A gesture either moves a cursor
//  through the same component methods the D-pad uses, or synthesizes a normal
//  button press that travels the regular Window::input() path (sounds, callbacks
//  and button-swap handling all included).
//

#ifndef ES_CORE_TOUCH_NAVIGATION_H
#define ES_CORE_TOUCH_NAVIGATION_H

#include <SDL2/SDL_events.h>

#include <glm/ext/vector_float2.hpp>

#include <string>

class TouchNavigation
{
public:
    static TouchNavigation& getInstance();

    // Called from InputManager::parseEvent() for SDL_FINGERDOWN/UP/MOTION and for
    // SDL_MOUSEBUTTONDOWN/UP/MOUSEMOTION respectively. Both return true when the
    // event was consumed by touch navigation (i.e. the feature is enabled and the
    // event belongs to the tracked pointer). parseMouseEvent() ignores the mouse
    // events that SDL synthesizes from touches (which == SDL_TOUCH_MOUSEID) so a
    // single tap can never dispatch twice.
    bool parseFingerEvent(const SDL_Event& event);
    bool parseMouseEvent(const SDL_Event& event);

    // Advances the fling (kinetic scroll) animation. Called once per frame from
    // Window::update().
    void update(const int deltaTime);

    // Drops any in-progress gesture and stops a running fling. Called when the
    // stale-input flush in main.cpp runs (return from a game or the Steam overlay)
    // so a finger-down consumed before a pause can't replay as a phantom gesture.
    void reset();

    // Synthesizes a press+release of the named action ("a", "b", "start",
    // "leftshoulder", ...) as DEVICE_TOUCH/TYPE_TOUCH Inputs through Window::input().
    // The id is resolved with InputConfig::getInputByName() on the touch config, so
    // the "InputSwapButtons" setting is honored automatically.
    // CONTRACT: callers inside the pointerInput() dispatch must invoke this as their
    // LAST action and return immediately, as the synthesized input may delete the
    // calling component (e.g. "b" closing the menu that dispatched the tap).
    static void synthesizeInput(const std::string& name);

    // Maps a help-prompt icon name ("a", "b", "l", "rt", ...) to the input config
    // action name it should synthesize, or "" for prompts that aren't tappable
    // actions (the directional composites).
    static std::string promptToConfigName(const std::string& prompt);

private:
    TouchNavigation() noexcept;

    enum class State {
        IDLE, // No pointer tracked.
        PENDING, // Pointer down, movement still within the tap slop.
        SCROLLING, // Movement exceeded the slop; SCROLL events are being dispatched.
        SWALLOWED // Gesture consumed at press time (screensaver wake etc.).
    };

    void pointerDown(const glm::vec2& position, const Uint32 timestamp);
    void pointerMove(const glm::vec2& position, const Uint32 timestamp);
    void pointerUp(const glm::vec2& position, const Uint32 timestamp);
    void secondFingerDown(const SDL_FingerID fingerId, const Uint32 timestamp);
    void secondFingerUp(const Uint32 timestamp);
    void cancelFling();

    // Maps physical window coordinates to UI (screen) coordinates, accounting for
    // the ScreenRotate setting. Only rotation 0 has been verified on-device; the
    // 90/180/270 formulas mirror the renderer's projection setup.
    glm::vec2 windowToUiCoords(const glm::vec2& windowPosition) const;

    bool touchNavigationEnabled() const;
    void logDebug(const std::string& message) const;

    State mState;
    bool mMousePointer; // The tracked pointer is a real mouse, not a finger.
    bool mMouseButtonDown; // Left mouse button currently held (real mouse only).
    SDL_FingerID mFingerId; // The tracked finger when mMousePointer is false.
    bool mSecondFingerActive;
    bool mHadSecondFinger; // A second finger participated at any point in the gesture.
    SDL_FingerID mSecondFingerId;
    Uint32 mSecondFingerDownTime;
    glm::vec2 mDownPosition; // Gesture origin, UI coordinates.
    glm::vec2 mLastPosition;
    Uint32 mLastEventTime;

    // Velocity sample window for fling detection: pointerUp computes the release
    // velocity from the distance covered since this sample was taken.
    glm::vec2 mVelocitySamplePosition;
    Uint32 mVelocitySampleTime;

    // Fling (kinetic scroll) state. While active, update() keeps dispatching
    // pointerScroll() events hit-tested at the gesture's original start point.
    bool mFlingActive;
    glm::vec2 mFlingStartPoint;
    glm::vec2 mFlingVelocity;
    void* mFlingTopGui; // Identity of Window::peekGui() at fling start (never
                        // dereferenced, only compared to detect GUI changes).
};

#endif // ES_CORE_TOUCH_NAVIGATION_H
