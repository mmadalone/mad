//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEmuInputMap.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageEmuInputMap.h"

#include "Window.h"
#include "guis/mad/GuiMadCaptureModal.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"

#include <functional>
#include <string>
#include <utility>
#include <vector>

GuiMadPageEmuInputMap::GuiMadPageEmuInputMap(GuiMadPanel* panel, const std::string& title,
                                             const std::string& emu)
    : MadLightgunPageBase {panel, title}
    , mEmu {emu}
{
}

void GuiMadPageEmuInputMap::build()
{
    if (!mBuilt) // on a refresh keep the current rows visible until the new ones swap in
        setLoadingText("Loading bindings…");
    pageRequest(
        mEmu + ".input_get", nullptr,
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

void GuiMadPageEmuInputMap::populate(const rapidjson::Value& result)
{
    beginColumn();
    const float pad {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.3f};

    const std::string note {MadJson::getString(result, "note")};
    if (MadJson::getBool(result, "running", false))
        addBlock("●  " + (note.empty() ? std::string("This emulator is running — close it before "
                                                     "changing bindings (it rewrites its config "
                                                     "on exit).")
                                       : note),
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Red), pad);
    else
        addBlock("Pick a row, then press the button you want bound to that action.",
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), pad);

    const rapidjson::Value& groups {MadJson::getMember(result, "groups")};
    if (!groups.IsArray()) {
        endColumn();
        return;
    }
    for (const rapidjson::Value& g : groups.GetArray()) {
        header(MadJson::getString(g, "title"));
        const rapidjson::Value& binds {MadJson::getMember(g, "binds")};
        if (!binds.IsArray())
            continue;
        // Capturable binds go in a wrapping button grid (true 4-way nav); a
        // non-capturable bind (e.g. PCSX2 d-pad/sticks for now) shows read-only.
        std::vector<std::pair<std::string, std::function<void()>>> row;
        for (const rapidjson::Value& b : binds.GetArray()) {
            const std::string id {MadJson::getString(b, "id")};
            const std::string label {MadJson::getString(b, "label", id)};
            const std::string kind {MadJson::getString(b, "kind", "btn")};
            const std::string val {MadJson::getString(b, "value")};
            const std::string shown {val.empty() ? "—" : val};
            if (MadJson::getBool(b, "capturable", false))
                row.emplace_back(label + ": " + shown,
                                 [this, id, label, kind] { captureFor(id, label, kind); });
            else
                addBlock("   " + label + ": " + shown, FONT_SIZE_SMALL,
                         MadTheme::color(MadColor::Secondary), 0.0f);
        }
        if (!row.empty())
            addButtonRow(row, false);
    }
    endColumn();
}

void GuiMadPageEmuInputMap::captureFor(const std::string& id, const std::string& label,
                                       const std::string& kind)
{
    std::weak_ptr<int> alive {pageAlive()};
    if (kind == "axis") {
        mWindow->pushGui(new GuiMadCaptureModal(
            mPanel, "axis", "Move the stick for " + label + "…",
            [this, alive, id, label](const GuiMadCaptureModal::Result* r) {
                if (alive.expired() || r == nullptr || r->axisToken.empty())
                    return;
                setBind(id, "axis", r->axisToken, "", label);
            }));
    }
    else if (kind == "gun") {
        mWindow->pushGui(new GuiMadCaptureModal(
            mPanel, "pointer", "Press a button or key for " + label + "…",
            [this, alive, id, label](const GuiMadCaptureModal::Result* r) {
                if (alive.expired() || r == nullptr || r->gunKind.empty())
                    return;
                setBind(id, "gun", r->gunValue, r->gunKind, label);
            }));
    }
    else {
        mWindow->pushGui(new GuiMadCaptureModal(
            mPanel, "identify", "Press a button for " + label + "…",
            [this, alive, id, label](const GuiMadCaptureModal::Result* r) {
                if (alive.expired() || r == nullptr || r->held.empty())
                    return;
                // Forward the RAW evdev button code; the emulator's backend maps
                // it to that emulator's binding token.
                setBind(id, "btn", std::to_string(r->held[0]), "", label);
            }));
    }
}

void GuiMadPageEmuInputMap::setBind(const std::string& id, const std::string& kind,
                                    const std::string& value, const std::string& gunKind,
                                    const std::string& label)
{
    pageRequest(
        mEmu + ".input_set",
        [id, kind, value, gunKind](MadJson::Writer& w) {
            w.Key("id");
            w.String(id.c_str(), static_cast<rapidjson::SizeType>(id.length()));
            w.Key("kind");
            w.String(kind.c_str(), static_cast<rapidjson::SizeType>(kind.length()));
            w.Key("value");
            w.String(value.c_str(), static_cast<rapidjson::SizeType>(value.length()));
            if (!gunKind.empty()) {
                w.Key("gun_kind");
                w.String(gunKind.c_str(), static_cast<rapidjson::SizeType>(gunKind.length()));
            }
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

std::vector<HelpPrompt> GuiMadPageEmuInputMap::getHelpPrompts()
{
    return {HelpPrompt("up/down/left/right", "choose"), HelpPrompt("a", "rebind"),
            HelpPrompt("b", "back")};
}
