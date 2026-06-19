//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRetroArchInput.h
//
//  MAD control panel: RetroArch keybindings (deck-patches). Grouped input binds
//  (face / d-pad / shoulders / sticks / start-select / system hotkeys / lightgun)
//  for a selectable player. Joypad buttons capture via the "identify" modal; analog
//  sticks via the "axis" modal (→ "±N"); lightgun (Sinden) buttons via the "pointer"
//  modal, which auto-detects a mouse button vs a keyboard key and writes the matching
//  retroarch.cfg variant. A "Start Sinden guns" button brings the guns up first.
//  Backend: retroarch.input_get / input_set / input_set_gun / sinden.driver.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETROARCH_INPUT_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETROARCH_INPUT_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scaffolding.

#include <map>
#include <memory>
#include <string>
#include <utility>
#include <vector>

class GuiMadPageRetroArchInput : public MadLightgunPageBase
{
public:
    GuiMadPageRetroArchInput(GuiMadPanel* panel);

    void build() override;
    void update(int deltaTime) override;
    bool input(InputConfig* config, Input input) override; // Start on a focused row clears it
    void onChildPopped() override {}
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void populate(const rapidjson::Value& result);
    void applySindenState(const bool running); // flip the Start/Stop label from driver state
    // Route an A-press to the right capture path by bind kind (btn/axis/gun).
    void captureFor(const std::string& key, const std::string& label, const std::string& kind);
    void captureBind(const std::string& key, const std::string& label);   // joypad button
    void captureAxis(const std::string& key, const std::string& label);   // analog stick
    void captureGun(const std::string& key, const std::string& label);    // mouse/keyboard
    void captureHotkey(const std::string& key, const std::string& label); // joypad OR mouse
    void setBind(const std::string& key, const std::string& value, const std::string& label);
    void setGun(const std::string& base, const std::string& kind, const std::string& value,
                const std::string& label);
    void setHotkey(const std::string& base, int code, int index, const std::string& label);
    void setHotkeyToken(const std::string& base, const std::string& token,
                        const std::string& label); // hat/d-pad direction (e.g. "h0up")
    void clearBind(const std::string& key, const std::string& kind,
                   const std::string& label);      // unbind (Start on a focused row)
    void applyTarget(const std::string& v); // set device-mode or global from picker value

    int mPlayer {1};
    // Device mode: when set, per-player binds read/write THIS controller's
    // RetroArch autoconfig — so they survive the controller-router's reserved-port
    // override at launch. Empty = global mode (legacy per-player retroarch.cfg
    // binds). Hotkeys + lightgun always stay global. mDevices caches input_get's
    // connected pads for the Target picker.
    std::string mDeviceVidpid;
    std::string mDeviceName;   // raw evdev name — RetroArch's vid:pid+name profile identity
    std::string mDeviceLabel;  // friendly display label (e.g. "X-Arcade P1")
    // Connected pads for the Target picker. The X-Arcade's two halves share one (vidpid,
    // name) profile but get distinct labels ("X-Arcade P1"/"P2") — display label, match name.
    struct PadEntry { std::string vidpid; std::string name; std::string label; };
    std::vector<PadEntry> mDevices;

    // ④ Clear: each bind row's button → its (key, kind, label), so input() can unbind the
    // FOCUSED row when Start is pressed (without opening the capture modal).
    struct BindRef { std::string key; std::string kind; std::string label; };
    std::map<GuiComponent*, BindRef> mBindingByComp;

    // "Start/Stop Sinden guns" toggle — its label is polled from sinden.status,
    // mirroring the Lightgun page's driver Start/Stop indicator.
    std::shared_ptr<ButtonComponent> mSindenButton;
    float mSindenButtonWidth {0.0f};
    int mSindenPollAccum {0};
    bool mSindenRunning {false};
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETROARCH_INPUT_H
