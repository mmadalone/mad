//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRestoreHub.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageRestoreHub.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackupRestore.h"
#include "guis/mad/pages/GuiMadPageBios.h"
#include "guis/mad/pages/GuiMadPageChooseTarget.h" // the standard source chooser (first step)
#include "guis/mad/pages/GuiMadPageEsde.h"
#include "guis/mad/widgets/MadTileGrid.h"

GuiMadPageRestoreHub::GuiMadPageRestoreHub(GuiMadPanel* panel)
    : MadPage {panel, "RESTORE"}
{
}

void GuiMadPageRestoreHub::build()
{
    std::vector<MadTileGrid::Tile> tiles;
    MadTileGrid::Tile game;
    game.key = "game";
    game.label = "Games";
    game.artPath = MadTheme::routerIconPath("per-game"); // reuse the game backup icon
    tiles.emplace_back(game);
    MadTileGrid::Tile settings;
    settings.key = "settings";
    settings.label = "Settings";
    settings.artPath = MadTheme::routerIconPath("backup-settings"); // dedicated Restore-hub Settings icon
    tiles.emplace_back(settings);
    MadTileGrid::Tile bios;
    bios.key = "bios";
    bios.label = "BIOS";
    bios.artPath = MadTheme::routerIconPath("backup-bios");
    tiles.emplace_back(bios);

    mGrid = std::make_shared<MadTileGrid>();
    mGrid->setPosition(mViewportPos.x, mViewportPos.y);
    mGrid->setSize(mViewportSize.x, mViewportSize.y);
    mGrid->setTiles(tiles);
    mGrid->setCursorIndex(mGridCookie);
    mGrid->setOnPick([this](const std::string& key) { onPick(key); });
    mGrid->onFocusGained();
    addChild(mGrid.get());
    mPanel->refreshHelpPrompts();
}

void GuiMadPageRestoreHub::onPick(const std::string& key)
{
    // Every category restores through the SAME standard flow: pick the SOURCE first (Local folder / a MEGA
    // set), then drill into what to restore. The chooser resolves a MadTarget and this page's lambda pushes
    // the matching drill page on top (ES-DE settings restore is STAGED to next boot, rule #3).
    GuiMadPanel* panel {mPanel};
    if (key == "game") {
        mPanel->pushPage(new GuiMadPageChooseTarget(
            mPanel, "restore", MadChooser::games(),
            [panel](const MadTarget& t) { panel->pushPage(new GuiMadPageBackupRestore(panel, "restore", t)); }));
    }
    else if (key == "settings") {
        mPanel->pushPage(new GuiMadPageChooseTarget(
            mPanel, "restore", MadChooser::esde(),
            [panel](const MadTarget& t) { panel->pushPage(new GuiMadPageEsde(panel, "restore", t)); }));
    }
    else if (key == "bios") {
        mPanel->pushPage(new GuiMadPageChooseTarget(
            mPanel, "restore", MadChooser::bios(),
            [panel](const MadTarget& t) { panel->pushPage(new GuiMadPageBios(panel, "restore", t)); }));
    }
}

bool GuiMadPageRestoreHub::input(InputConfig* config, Input input)
{
    return mGrid != nullptr && mGrid->input(config, input);
}

void GuiMadPageRestoreHub::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageRestoreHub::getHelpPrompts()
{
    return mGrid != nullptr ? mGrid->getHelpPrompts() : std::vector<HelpPrompt>();
}

void GuiMadPageRestoreHub::onSaveFocus()
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
}

void GuiMadPageRestoreHub::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mGridCookie);
}
