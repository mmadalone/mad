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

    addButtonRow({
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
        {"Start Sinden guns", [this] {
             footer()->flash("Starting Sinden guns…", 8000, false);
             pageRequest(
                 "sinden.driver",
                 [](MadJson::Writer& w) {
                     w.Key("action");
                     w.String("start");
                 },
                 [this](bool ok, const rapidjson::Value& p) {
                     footer()->flash(ok ? "Sinden guns starting."
                                        : "Couldn't start Sinden: " +
                                              MadJson::getString(p, "message", "error"),
                                     4000, !ok);
                 },
                 20000);
         }},
    });

    const rapidjson::Value& groups {MadJson::getMember(result, "groups")};
    if (groups.IsArray()) {
        for (const rapidjson::Value& g : groups.GetArray()) {
            header(MadJson::getString(g, "title"));
            const rapidjson::Value& binds {MadJson::getMember(g, "binds")};
            if (!binds.IsArray())
                continue;
            // Capturable binds go side-by-side in a wrapping grid — left/right walks a
            // line, up/down moves between lines (true 4-way nav; each wrapped line is
            // its own focus row). Stick/gun binds need the deferred capture path, so
            // they're just noted.
            std::vector<std::pair<std::string, std::function<void()>>> row;
            int deferred {0};
            for (const rapidjson::Value& b : binds.GetArray()) {
                const std::string key {MadJson::getString(b, "key")};
                const std::string label {MadJson::getString(b, "label", key)};
                const std::string val {MadJson::getString(b, "value")};
                const std::string shown {(val.empty() || val == "nul") ? "—" : val};
                if (MadJson::getBool(b, "capturable", false))
                    row.emplace_back(label + ": " + shown,
                                     [this, key, label] { captureBind(key, label); });
                else
                    ++deferred;
            }
            if (!row.empty())
                addButtonRow(row, false);
            if (deferred > 0)
                addBlock("   (stick / lightgun mapping coming soon)", FONT_SIZE_SMALL,
                         MadTheme::color(MadColor::Secondary), 0.0f);
        }
    }
    endColumn();
}

void GuiMadPageRetroArchInput::captureBind(const std::string& key, const std::string& label)
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiMadCaptureModal(
        mPanel, "identify", "Press a button for " + label + "…",
        [this, alive, key, label](const GuiMadCaptureModal::Result* r) {
            if (alive.expired() || r == nullptr || r->held.empty())
                return;
            const int idx {r->held[0] - kBtnBase};
            if (idx < 0) {
                footer()->flash("That input can't be used as a RetroArch button.", 4000, true);
                return;
            }
            setBind(key, std::to_string(idx), label);
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

std::vector<HelpPrompt> GuiMadPageRetroArchInput::getHelpPrompts()
{
    return {HelpPrompt("up/down/left/right", "choose"), HelpPrompt("a", "rebind"),
            HelpPrompt("b", "back")};
}
