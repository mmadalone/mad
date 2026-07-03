//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRetroArchGame.h
//
//  MAD control panel: RetroArch hub -> Per-game -> one system's gameview
//  (deck-patches). The two-pane media+info browser lives in the shared base
//  GuiMadPagePergameBrowser; this subclass adds only the RA-specific bits:
//  the sibling "cores" array + an X core picker (which core the per-game
//  Settings / Input remap edits target), the "<system>:<stem>" game identity,
//  a "Core: <name>" subtitle + an "Edit: <core>" preview line, and the fixed
//  Settings / Input remap / Controllers sub-menu opened on A. Backend:
//  ragame.games.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETRO_ARCH_GAME_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETRO_ARCH_GAME_H

#include "guis/mad/pages/GuiMadPagePergameBrowser.h"

#include <string>
#include <vector>

class GuiMadPageRetroArchGame : public GuiMadPagePergameBrowser
{
public:
    GuiMadPageRetroArchGame(GuiMadPanel* panel, const std::string& system);

protected:
    void build() override; // reset the picked core, then the shared build().
    void writeGamesArgs(MadJson::Writer& w) override;         // ragame.games wants {system}.
    std::string gameId(const rapidjson::Value& g) override;   // "<system>:<stem>".
    void parsePayloadExtra(const rapidjson::Value& payload) override; // the "cores" sibling.
    void perGameExtra(const rapidjson::Value& g, Game& out) override; // "Core: <name>" subtitle.
    void openGame(int i) override;                            // Settings / Input remap / Controllers.
    std::string previewHeadLines(const Game& g) override;     // the "Edit: <core>" line.
    std::string defaultSummary() override;                    // the 3-line default block.
    bool onExtraButton(InputConfig* config, Input input) override; // X = core picker.
    void extraHelpPrompts(std::vector<HelpPrompt>& prompts) override; // X = core.

private:
    // X, when the system has more than one core: pick which core the per-game
    // Settings / Input remap RPCs target (mEditCore), or "All cores".
    void openCorePicker();

    std::vector<std::string> mCores; // top-level "cores" from ragame.games; >1 = multi-core system
    // Picked core to target for per-game Settings/Input remap edits; "" == All
    // cores (unchanged behavior). Reset to "" when the page is (re)built.
    std::string mEditCore;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETRO_ARCH_GAME_H
