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
#include "guis/mad/pages/GuiMadPageEmu.h"
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
    MadTileGrid::Tile emu;
    emu.key = "emucfg";
    emu.label = "Emulator config";
    emu.artPath = MadTheme::routerIconPath("backup-emu");
    tiles.emplace_back(emu);

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
    // Games + BIOS restore go straight to the systems tiles, with the SOURCE (which backup / MEGA) chosen
    // from a bar at the TOP of that page. ES-DE settings restore keeps the source chooser first (it is
    // versioned + its page has no room for the bar), and stages to next boot (rule #3).
    GuiMadPanel* panel {mPanel};
    if (key == "game")
        mPanel->pushPage(new GuiMadPageBackupRestore(mPanel, "restore"));
    else if (key == "bios")
        mPanel->pushPage(new GuiMadPageBios(mPanel, "restore"));
    else if (key == "settings")
        mPanel->pushPage(new GuiMadPageChooseTarget(
            mPanel, "restore", MadChooser::esde(),
            [panel](const MadTarget& t) { panel->pushPage(new GuiMadPageEsde(panel, "restore", t)); }));
    else if (key == "emucfg")
        mPanel->pushPage(new GuiMadPageChooseTarget(
            mPanel, "restore", MadChooser::emucfg(),
            [panel](const MadTarget& t) { panel->pushPage(new GuiMadPageEmu(panel, "restore", t)); }));
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
