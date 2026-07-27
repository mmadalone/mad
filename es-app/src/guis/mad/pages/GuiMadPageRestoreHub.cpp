//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRestoreHub.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageRestoreHub.h"

#include "Window.h"
#include "guis/mad/GuiMadFolderPicker.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackupRestore.h"
#include "guis/mad/pages/GuiMadPageBios.h"
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
    game.label = "Game";
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
    if (key == "game") {
        mPanel->pushPage(new GuiMadPageBackupRestore(mPanel, "restore"));
        return;
    }
    if (key == "settings") {
        footer()->flash("ES-DE settings restore is coming in a later update.", 4000, false);
        return;
    }
    if (key == "bios") {
        // Pick the source, then open the BIOS restore flow: ON THIS DECK -> a backup folder; MEGA CLOUD ->
        // the "cloud" sentinel (GuiMadPageBios then lists the MEGA BIOS-backup sets).
        std::weak_ptr<int> alive {pageAlive()};
        mWindow->pushGui(new MadMsgBox(
            "Restore BIOS from where?",
            "ON THIS DECK",
            [this, alive] {
                if (alive.expired())
                    return;
                mWindow->pushGui(new GuiMadFolderPicker(
                    [this, alive](const std::string& folder) {
                        if (alive.expired() || folder.empty()) // empty == cancelled
                            return;
                        mPanel->pushPage(new GuiMadPageBios(mPanel, "restore", folder));
                    },
                    "PICK A BACKUP FOLDER"));
            },
            "MEGA CLOUD",
            [this, alive] {
                if (!alive.expired())
                    mPanel->pushPage(new GuiMadPageBios(mPanel, "restore", "cloud"));
            }));
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
