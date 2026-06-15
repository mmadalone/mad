//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageGamePicker.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageGamePicker.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageEmuSettings.h"

#include <string>
#include <vector>

GuiMadPageGamePicker::GuiMadPageGamePicker(GuiMadPanel* panel, const std::string& title,
                                           const std::string& ns)
    : MadLightgunPageBase {panel, title}
    , mNs {ns}
{
}

void GuiMadPageGamePicker::build()
{
    setLoadingText("Loading games…");
    pageRequest(
        mNs + ".games", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load games: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            populate(payload);
        },
        8000);
}

void GuiMadPageGamePicker::populate(const rapidjson::Value& result)
{
    beginColumn();
    const float pad {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f};
    const rapidjson::Value& games {MadJson::getMember(result, "games")};
    if (!games.IsArray() || games.Size() == 0) {
        addBlock("No games found yet — play a game in this emulator once so it "
                 "appears here.",
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), pad);
        endColumn();
        return;
    }
    addBlock("Pick a game to edit just its settings (overrides the global defaults; "
             "“• custom” = it already has an override).",
             FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), pad);

    const std::string ns {mNs};
    for (const rapidjson::Value& g : games.GetArray()) {
        const std::string tid {MadJson::getString(g, "titleid")};
        const std::string name {MadJson::getString(g, "name", tid)};
        const bool hasOverride {MadJson::getBool(g, "override", false)};
        const std::string label {hasOverride ? name + "   • custom" : name};
        addButton(label, [this, ns, tid, name] {
            mPanel->pushPage(
                new GuiMadPageEmuSettings(mPanel, name + " — Settings", ns, "titleid", tid));
        });
    }
    endColumn();
}

std::vector<HelpPrompt> GuiMadPageGamePicker::getHelpPrompts()
{
    return {HelpPrompt("up/down", "choose"), HelpPrompt("a", "edit"),
            HelpPrompt("b", "back")};
}
