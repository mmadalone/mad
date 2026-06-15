//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageGamePicker.h
//
//  MAD control panel: per-game settings picker (deck-patches). Lists a standalone
//  emulator's games (by friendly name, from "<ns>.games") and, on pick, opens the
//  generic GuiMadPageEmuSettings targeting that game's per-game override (passing
//  ("titleid", <id>) as the request context). Used by the Switch emulators'
//  "Per-game settings" section.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_GAME_PICKER_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_GAME_PICKER_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scaffolding.

#include <string>
#include <vector>

class GuiMadPageGamePicker : public MadLightgunPageBase
{
public:
    // ns = RPC namespace; the list comes from "<ns>.games", and each pick opens
    // "<ns>.get/.set" with a "titleid" context.
    GuiMadPageGamePicker(GuiMadPanel* panel, const std::string& title, const std::string& ns);

    void build() override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void populate(const rapidjson::Value& result);

    std::string mNs;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_GAME_PICKER_H
