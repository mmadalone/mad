//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRetroArchGame.h
//
//  MAD control panel: RetroArch hub -> Per-game -> one system's gameview
//  (deck-patches). A virtualized list of the system's games (LEFT) with a
//  compact "applied per-game overrides" text preview (RIGHT, refreshed
//  LOCALLY from the preloaded payload on every cursor move — no per-cursor
//  RPC) — cloned from GuiMadPageBezelPerGame's two-panel MadVirtualList
//  skeleton, swapping the bezel ImageComponent preview for a TextComponent
//  summary. Override games get a "* " row prefix + a distinct row color. The
//  preview also carries a "Core: <name>" subtitle under the game name — the
//  RetroArch core the backend resolved as the one the LAUNCHED command
//  actually reads (Phase 5a: core-awareness base) — omitted when unresolvable
//  (a standalone system). Y opens ES-DE's on-screen keyboard to filter by
//  name or rom stem. A on a game pushes the per-game Settings / Input remap /
//  Controllers chooser (GuiMadPageStandaloneSections, the same in-memory-
//  Section "inputmenu" pattern GuiMadPageGamePicker uses). Backend: ragame.games.
//
//  RIGHT pane also carries ES-DE-parity media: the highlighted game's art (via
//  MadVideoComponent's embedded static image) with its preview VIDEO starting
//  after a short hover, resolved straight from ES-DE's own FileData (so it
//  inherits the user's MediaDirectory + gamelist media/audio settings, no
//  backend round trip) — the mPreview text summary sits below it.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETRO_ARCH_GAME_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETRO_ARCH_GAME_H

#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadVideoComponent.h"
#include "guis/mad/widgets/MadVirtualList.h"

#include <string>
#include <unordered_map>
#include <vector>

class FileData;

class GuiMadPageRetroArchGame : public MadPage
{
public:
    GuiMadPageRetroArchGame(GuiMadPanel* panel, const std::string& system);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override; // a per-game edit may flip the "* " badge; re-issue the list.
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct Game {
        std::string stem;    // rom stem — the game-identity half of "<system>:<stem>"
        std::string name;    // gamelist <name> — the row + preview display text
        bool overrides;
        std::string summary; // "" (no overrides) or the 3 pre-formatted preview lines
        std::string core;    // launched RetroArch core display name, or "" (Phase 5a)
    };
    static unsigned int rowColor(const bool overrides); // override = a distinct color, else Primary

    void ensureWidgets();               // create the header / list / preview once
    void requestGames(const bool keepCursor); // issue ragame.games, then populate()
    void populate(const bool keepCursor = false); // (re)build the filtered list + preview pane
    void updatePreview();               // LOCAL — no RPC
    void openSearch();
    void openGame(int i); // A / select — push the per-game Settings/Input remap/Controllers chooser

    std::string mSystem;
    std::string mFilter;
    std::vector<Game> mGames; // all games for the system
    std::vector<Game> mShown; // filtered subset, parallel to the list rows
    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    std::shared_ptr<MadVideoComponent> mVideo; // art (embedded static image) + preview video
    std::shared_ptr<TextComponent> mPreview;
    // stem -> FileData*, built once in ensureWidgets() from the live SystemData
    // tree — media resolution only (ragame.games' own per-game payload stays
    // the source of truth for the "* " badge / overrides summary).
    std::unordered_map<std::string, FileData*> mByStem;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETRO_ARCH_GAME_H
