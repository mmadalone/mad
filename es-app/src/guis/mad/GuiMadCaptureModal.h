//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadCaptureModal.h
//
//  MAD control panel: press-to-identify / press-a-combo modal (deck-patches).
//  Window-pushed on top of GuiMadPanel; drives one capture.button stream and
//  reports the result (or null on cancel/timeout/error) through a callback.
//
//  While this modal is up, Window updates only the TOP gui — GuiMadPanel's
//  update() (and thus its backend poll) does NOT run, so the modal polls the
//  shared backend from its OWN update(). The physical button press also
//  reaches SDL, which is why input() swallows everything except B.
//

#ifndef ES_APP_GUIS_MAD_GUI_MAD_CAPTURE_MODAL_H
#define ES_APP_GUIS_MAD_GUI_MAD_CAPTURE_MODAL_H

#include "components/BackgroundComponent.h"
#include "components/BusyComponent.h"
#include "components/TextComponent.h"
#include "guis/mad/MadBackend.h"
#include "renderers/Renderer.h"

#include <functional>
#include <memory>
#include <string>
#include <vector>

class GuiMadPanel;

class GuiMadCaptureModal : public GuiComponent
{
public:
    struct Result {
        std::vector<int> held;
        std::vector<std::string> names;
        // axis mode: the RetroArch axis token, e.g. "+0" / "-3".
        std::string axisToken;
        // identify mode, single stick direction (hat): a RetroArch hat token,
        // e.g. "h0up" — a valid *_btn value, for binding the X-Arcade joystick.
        std::string bindToken;
        // pointer mode: gunKind is "mouse" or "key"; gunValue is the mouse-button
        // number (as a string) or the RetroArch keyname.
        std::string gunKind;
        std::string gunValue;
        // Device fields from the capture's Device payload (identify mode);
        // hasDevice is false when the backend reported device: null.
        bool hasDevice {false};
        std::string deviceName;
        std::string devicePinId;
        std::string devicePinKind;
        std::string devicePort;
        std::string deviceLabel;
    };
    // result is nullptr on cancel, timeout, error or backend death.
    using ResultCallback = std::function<void(const Result* result)>;

    // mode is "identify" / "combo" (joypad), "axis" (analog stick) or "pointer"
    // (mouse button / keyboard key, for lightguns).
    GuiMadCaptureModal(GuiMadPanel* panel,
                       const std::string& mode,
                       const std::string& prompt,
                       const ResultCallback& callback);
    ~GuiMadCaptureModal();

    bool input(InputConfig* config, Input input) override;
    void update(int deltaTime) override;
    // Window renders only the bottom gui + this (top) modal, so the panel in
    // between would vanish during captures: draw the panel first (it paints an
    // opaque backdrop), then this modal's own frame.
    void render(const glm::mat4& parentTrans) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void onStreamData(const rapidjson::Value& data);
    void setMessage(const std::string& text, const bool busy);
    // Shows text briefly, then pops with a null result.
    void failSoon(const std::string& text);
    // Pops the modal and invokes the callback (with the result or nullptr).
    void finish(const bool produceResult);

    Renderer* mRenderer;
    GuiMadPanel* mPanel;
    MadBackend* mBackend;
    BackgroundComponent mBackground;
    std::shared_ptr<TextComponent> mMessage;
    BusyComponent mBusy;

    ResultCallback mCallback;
    std::string mPrompt;
    std::string mStreamToken;
    Result mResult;
    bool mFinished;
    // True once {ready:true} arrived: B (BTN_EAST) is itself a capturable face
    // button, so from then on input() swallows it too — no cancel gesture while
    // armed; the daemon's 15s timeout (or the result) ends the capture.
    bool mArmed;
    // axis/pointer modes capture stick/mouse/keyboard events, so the gamepad B is
    // never a capture target — B can cancel at any time (unlike the joypad modes,
    // where B is itself a capturable face button once armed).
    bool mCancelAnytime;
    int mCloseTimer; // > 0: counting down to a null-result close.
    // Live "auto-cancels in Ns" countdown for the joypad capture (cosmetic; the
    // daemon's timeout is authoritative). mCountdownMs ticks down from the capture
    // timeout once armed; mShownSecs is the last whole second rendered (-1 = none).
    int mCountdownMs;
    int mShownSecs;

    std::shared_ptr<int> mAliveToken;
};

#endif // ES_APP_GUIS_MAD_GUI_MAD_CAPTURE_MODAL_H
