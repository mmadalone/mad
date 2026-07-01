//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageGamePicker.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageGamePicker.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageEmuInputMap.h"
#include "guis/mad/pages/GuiMadPageEmuSettings.h"
#include "guis/mad/pages/GuiMadPageLindberghPads.h"
#include "guis/mad/pages/GuiMadPagePergamePads.h"

#include <string>
#include <vector>

GuiMadPageGamePicker::GuiMadPageGamePicker(GuiMadPanel* panel, const std::string& title,
                                           const std::string& ns, const std::string& target)
    : MadLightgunPageBase {panel, title}
    , mNs {ns}
    , mTarget {target}
{
}

void GuiMadPageGamePicker::build()
{
    setLoadingText("Loading games…");
    requestGames();
}

void GuiMadPageGamePicker::onChildPopped()
{
    // Picking a game and creating its FIRST per-game override flips the
    // "• custom" badge truth; re-issue the games request so the list rebuilds.
    // No spinner: the old list stays visible until the fresh data lands. The
    // focus cursor + scroll survive — beginColumn()/endColumn() save and
    // restore them, and the panel calls onRestoreFocus() before this.
    requestGames();
}

void GuiMadPageGamePicker::requestGames()
{
    const bool pads {mTarget == "pads"};
    pageRequest(
        mNs + ".games",
        [pads](MadJson::Writer& writer) {
            if (pads) {                 // non-lightgun filter for the pads -> players target
                writer.Key("pads");
                writer.Bool(true);
            }
        },
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
    const bool pads {mTarget == "pads"};
    const bool input {mTarget == "input"};
    const rapidjson::Value& games {MadJson::getMember(result, "games")};
    if (!games.IsArray() || games.Size() == 0) {
        addBlock(pads && mNs == "lindbergh"
                     ? "No non-lightgun Lindbergh games found (lightgun games use the gun, not pads)."
                     : "No games found yet — play a game in this emulator once so it appears here.",
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), pad);
        endColumn();
        return;
    }
    // The pads target serves two pages: Lindbergh (reorder + per-pad JVS button map) and PCSX2
    // per-game (reorder only; PCSX2 button remaps live on its own Per-game input page).
    const std::string padsIntro {
        mNs == "lindbergh"
            ? "Pick a game, then choose which pad is each player and map each pad's buttons."
            : "Pick a game, then set which controller is each player (top = Player 1) for that game."};
    addBlock(pads    ? padsIntro
             : input ? "Pick a game to set its per-game input (USB ports, Player 2, button remaps; "
                       "“• custom” = it already has an override)."
                     : "Pick a game to edit just its settings (overrides the global defaults; "
                       "“• custom” = it already has an override).",
             FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), pad);

    const std::string ns {mNs};
    for (const rapidjson::Value& g : games.GetArray()) {
        const std::string tid {MadJson::getString(g, "titleid")};
        const std::string name {MadJson::getString(g, "name", tid)};
        const bool hasOverride {!pads && MadJson::getBool(g, "override", false)};
        const std::string label {hasOverride ? name + "   • custom" : name};
        addButton(label, [this, ns, tid, name, pads, input] {
            if (pads) {
                if (ns == "lindbergh")
                    mPanel->pushPage(
                        new GuiMadPageLindberghPads(mPanel, name + " — Controllers", tid));
                else
                    mPanel->pushPage(
                        new GuiMadPagePergamePads(mPanel, name + " — Controllers", ns, tid));
            }
            else if (input)
                mPanel->pushPage(
                    new GuiMadPageEmuInputMap(mPanel, name + " — Input", ns, "titleid", tid));
            else
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
