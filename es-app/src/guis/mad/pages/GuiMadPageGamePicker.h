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
#include "guis/mad/pages/GuiMadPageStandaloneSections.h" // Section (settingsmenu per-game leaves).

#include <string>
#include <vector>

class GuiMadPageGamePicker : public MadLightgunPageBase
{
public:
    // ns = RPC namespace; the list comes from "<ns>.games", and each pick opens
    // "<ns>.get/.set" with a "titleid" context. target "settings" (default) opens the
    // per-game settings editor; "pads" filters to non-lightgun games (passing pads:true)
    // and opens the per-game pads -> players page (Lindbergh).
    // target "settingsmenu" opens, on game-pick, a sub-menu of `menuSections` (per-game leaves)
    // with the picked titleid injected into each leaf's ctxVal.
    GuiMadPageGamePicker(GuiMadPanel* panel, const std::string& title, const std::string& ns,
                         const std::string& target = "settings",
                         const std::vector<GuiMadPageStandaloneSections::Section>& menuSections = {},
                         const std::string& context = "");

    void build() override;
    void onChildPopped() override; // A new per-game override flips the "• custom" badge; reload.
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void requestGames(); // Issues "<ns>.games" and populate()s the result.
    void populate(const rapidjson::Value& result);

    std::string mNs;
    std::string mTarget;
    std::vector<GuiMadPageStandaloneSections::Section> mMenuSections; // settingsmenu: per-game leaves
    std::string mContext; // "docked"|"handheld" for the input page a pick opens (empty = docked)
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_GAME_PICKER_H
