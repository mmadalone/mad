//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRetroArchSystems.h
//
//  MAD control panel: RetroArch hub -> Per-game -> Systems overview
//  (deck-patches). A single-grid picker, one tile per present RetroArch
//  system (name, "<N> games" sublabel, console art) — cloned from the
//  single-grid priority-picker skeleton (since removed in the Phase 4 cleanup),
//  NOT the dual-grid GuiMadPagePriority root (the priority RPC carries no
//  game count, and a systems-only list is cleaner as its own page). Backend:
//  ragame.systems. Picking a tile pushes GuiMadPageRetroArchGame for that
//  system.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETRO_ARCH_SYSTEMS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETRO_ARCH_SYSTEMS_H

#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadTileGrid.h"

#include <string>

class GuiMadPageRetroArchSystems : public MadPage
{
public:
    // handheld = true: the On-the-go per-game HANDHELD input variant -- picking a game jumps straight
    // to its handheld input editor (ragamehh) instead of the permanent Settings/Input/Controllers menu.
    GuiMadPageRetroArchSystems(GuiMadPanel* panel, const std::string& title, bool handheld = false);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override {} // system list/counts don't change while browsing.

private:
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<MadTileGrid> mGrid;
    bool mHandheld {false};
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETRO_ARCH_SYSTEMS_H
