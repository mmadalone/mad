//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRetroArchInput.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageRetroArchInput.h"

#include "Window.h"
#include "guis/mad/GuiMadCaptureModal.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendChoice (player picker)

#include <algorithm>
#include <functional>
#include <string>
#include <utility>
#include <vector>

namespace
{
    constexpr int kBtnBase {0x130}; // evdev BTN_SOUTH; RA joypad index = code - 0x130
} // namespace

GuiMadPageRetroArchInput::GuiMadPageRetroArchInput(GuiMadPanel* panel)
    : MadLightgunPageBase {panel, "RETROARCH INPUT"}
{
}

void GuiMadPageRetroArchInput::build()
{
    if (!mBuilt) // on a refresh keep the current rows visible until the new ones swap in
        setLoadingText("Loading bindings…");
    const int player {mPlayer};
    pageRequest(
        "retroarch.input_get",
        [player](MadJson::Writer& w) {
            w.Key("player");
            w.Int(player);
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load input bindings: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            populate(payload);
        },
        8000);
}

void GuiMadPageRetroArchInput::update(int deltaTime)
{
    // Keep the "Start/Stop Sinden guns" label in sync with the driver: Start/Stop
    // are detached scripts that take a few seconds, so poll the daemon's pgrep
    // state (same cadence as the Lightgun page).
    mSindenPollAccum += deltaTime;
    if (mSindenPollAccum >= 2000 && mSindenButton != nullptr) {
        mSindenPollAccum = 0;
        std::weak_ptr<int> alive {pageAlive()};
        pageRequest("sinden.status", nullptr,
                    [this, alive](bool ok, const rapidjson::Value& payload) {
                        if (alive.expired() || !ok)
                            return;
                        applySindenState(MadJson::getBool(payload, "driver_running"));
                    });
    }
    MadLightgunPageBase::update(deltaTime);
}

void GuiMadPageRetroArchInput::applySindenState(const bool running)
{
    if (mSindenButton == nullptr || running == mSindenRunning)
        return; // already showing the right label — avoid setText churn
    mSindenRunning = running;
    const std::string label {running ? "Stop Sinden guns" : "Start Sinden guns"};
    mSindenButton->setText(label, label);
    mSindenButton->setSize(std::max(mSindenButtonWidth, mSindenButton->getSize().x),
                           mSindenButton->getSize().y);
}

void GuiMadPageRetroArchInput::populate(const rapidjson::Value& result)
{
    beginColumn();
    const float pad {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.3f};

    if (MadJson::getBool(result, "running", false))
        addBlock("●  RetroArch is running — close it before changing bindings (it rewrites its "
                 "config on exit).",
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Red), pad);
    else
        addBlock("Map RetroArch buttons + hotkeys. These are global binds (per port, not per "
                 "physical pad). Pick a player, then a row to rebind.",
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), pad);

    auto madTopRow = addButtonRow({
        {"Player: " + std::to_string(mPlayer),
         [this] {
             std::weak_ptr<int> alive {pageAlive()};
             const std::vector<std::pair<std::string, std::string>> opts {
                 {"1", "Player 1"}, {"2", "Player 2"}, {"3", "Player 3"}, {"4", "Player 4"}};
             mPanel->pushPage(new GuiMadPageBackendChoice(
                 mPanel, "Player", "", opts, std::to_string(mPlayer),
                 [this, alive](const std::string& v) {
                     if (alive.expired())
                         return;
                     try {
                         mPlayer = std::stoi(v);
                     }
                     catch (...) {
                     }
                     build();
                 }));
         }},
        // Start/Stop Sinden guns: the label flips with the driver state (polled in
        // update(), like the Lightgun page's Start/Stop indicator).
        {mSindenRunning ? "Stop Sinden guns" : "Start Sinden guns",
         [this] {
             const std::string action {mSindenRunning ? "stop" : "start"};
             footer()->flash(mSindenRunning ? "Stopping Sinden guns…" : "Starting Sinden guns…",
                             8000, false);
             pageRequest(
                 "sinden.driver",
                 [action](MadJson::Writer& w) {
                     w.Key("action");
                     w.String(action.c_str(), static_cast<rapidjson::SizeType>(action.length()));
                 },
                 [this](bool ok, const rapidjson::Value& p) {
                     footer()->flash(ok ? "Sinden guns toggling."
                                        : "Couldn't toggle Sinden: " +
                                              MadJson::getString(p, "message", "error"),
                                     4000, !ok);
                 },
                 20000);
         }},
    });
    // Hold the Sinden button so update() can flip its label; pin its build-time
    // width so the shorter "Stop" label doesn't shift the row.
    mSindenButton = madTopRow.empty() ? nullptr : madTopRow.back();
    if (mSindenButton != nullptr)
        mSindenButtonWidth = mSindenButton->getSize().x;

    const rapidjson::Value& groups {MadJson::getMember(result, "groups")};
    if (groups.IsArray()) {
        for (const rapidjson::Value& g : groups.GetArray()) {
            header(MadJson::getString(g, "title"));
            const rapidjson::Value& binds {MadJson::getMember(g, "binds")};
            if (!binds.IsArray())
                continue;
            // Binds go side-by-side in a wrapping grid — left/right walks a line,
            // up/down moves between lines (true 4-way nav; each wrapped line is its
            // own focus row). A-press routes by kind: joypad button, analog axis, or
            // lightgun (mouse/keyboard) capture.
            std::vector<std::pair<std::string, std::function<void()>>> row;
            for (const rapidjson::Value& b : binds.GetArray()) {
                const std::string key {MadJson::getString(b, "key")};
                const std::string label {MadJson::getString(b, "label", key)};
                const std::string kind {MadJson::getString(b, "kind", "btn")};
                const std::string val {MadJson::getString(b, "value")};
                const std::string shown {(val.empty() || val == "nul") ? "—" : val};
                if (MadJson::getBool(b, "capturable", false))
                    row.emplace_back(label + ": " + shown,
                                     [this, key, label, kind] { captureFor(key, label, kind); });
            }
            if (!row.empty())
                addButtonRow(row, false);
        }
    }
    endColumn();
}

void GuiMadPageRetroArchInput::captureFor(const std::string& key, const std::string& label,
                                          const std::string& kind)
{
    if (kind == "axis")
        captureAxis(key, label);
    else if (kind == "gun")
        captureGun(key, label);
    else
        captureBind(key, label);
}

void GuiMadPageRetroArchInput::captureAxis(const std::string& key, const std::string& label)
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiMadCaptureModal(
        mPanel, "axis", "Move the stick for " + label + "…",
        [this, alive, key, label](const GuiMadCaptureModal::Result* r) {
            if (alive.expired() || r == nullptr || r->axisToken.empty())
                return;
            setBind(key, r->axisToken, label); // axis token is a plain input_set value
        }));
}

void GuiMadPageRetroArchInput::captureGun(const std::string& key, const std::string& label)
{
    // `key` is the full cfg key (input_player<N>_gun_<x>); input_set_gun wants the
    // base action (gun_<x>) + the player separately.
    const std::string pfx {"input_player" + std::to_string(mPlayer) + "_"};
    std::string base {key};
    if (base.rfind(pfx, 0) == 0)
        base = base.substr(pfx.length());
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiMadCaptureModal(
        mPanel, "pointer", "Press a gun button or key for " + label + "…",
        [this, alive, base, label](const GuiMadCaptureModal::Result* r) {
            if (alive.expired() || r == nullptr || r->gunKind.empty())
                return;
            setGun(base, r->gunKind, r->gunValue, label);
        }));
}

void GuiMadPageRetroArchInput::captureBind(const std::string& key, const std::string& label)
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiMadCaptureModal(
        mPanel, "identify", "Press a button (or push the stick) for " + label + "…",
        [this, alive, key, label](const GuiMadCaptureModal::Result* r) {
            if (alive.expired() || r == nullptr)
                return;
            if (!r->held.empty()) {
                const int idx {r->held[0] - kBtnBase};
                if (idx < 0) {
                    footer()->flash("That input can't be used as a RetroArch button.", 4000, true);
                    return;
                }
                setBind(key, std::to_string(idx), label);
            }
            else if (!r->bindToken.empty()) {
                // X-Arcade joystick / d-pad: a RetroArch hat token (e.g. "h0up"),
                // a valid *_btn value written straight through.
                setBind(key, r->bindToken, label);
            }
            else {
                footer()->flash("That input can't be used as a RetroArch button.", 4000, true);
            }
        }));
}

void GuiMadPageRetroArchInput::setBind(const std::string& key, const std::string& value,
                                       const std::string& label)
{
    pageRequest(
        "retroarch.input_set",
        [key, value](MadJson::Writer& w) {
            w.Key("key");
            w.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            w.Key("value");
            w.String(value.c_str(), static_cast<rapidjson::SizeType>(value.length()));
        },
        [this, label](bool ok, const rapidjson::Value& p) {
            if (!ok) {
                footer()->flash("Couldn't set " + label + ": " +
                                    MadJson::getString(p, "message", "error"),
                                4000, true);
                return;
            }
            footer()->flash("Set " + label, 2500, false);
            build(); // refresh the shown values
        });
}

void GuiMadPageRetroArchInput::setGun(const std::string& base, const std::string& kind,
                                      const std::string& value, const std::string& label)
{
    const int player {mPlayer};
    pageRequest(
        "retroarch.input_set_gun",
        [player, base, kind, value](MadJson::Writer& w) {
            w.Key("player");
            w.Int(player);
            w.Key("base");
            w.String(base.c_str(), static_cast<rapidjson::SizeType>(base.length()));
            w.Key("kind");
            w.String(kind.c_str(), static_cast<rapidjson::SizeType>(kind.length()));
            w.Key("value");
            w.String(value.c_str(), static_cast<rapidjson::SizeType>(value.length()));
        },
        [this, label](bool ok, const rapidjson::Value& p) {
            if (!ok) {
                footer()->flash("Couldn't set " + label + ": " +
                                    MadJson::getString(p, "message", "error"),
                                4000, true);
                return;
            }
            footer()->flash("Set " + label, 2500, false);
            build(); // refresh the shown values
        });
}

std::vector<HelpPrompt> GuiMadPageRetroArchInput::getHelpPrompts()
{
    return {HelpPrompt("up/down/left/right", "choose"), HelpPrompt("a", "rebind"),
            HelpPrompt("b", "back")};
}
