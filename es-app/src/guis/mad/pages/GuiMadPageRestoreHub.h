//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRestoreHub.h
//
//  The RESTORE category hub (deck-patches), reached from the Backup page's "Restore" tile. It shows a
//  tile grid of what you can restore - Game / Settings / BIOS - and routes each to that category's restore
//  flow. It owns NO running op (it only navigates), so it is a plain tile page, not a durable root:
//    Game     -> GuiMadPageBackupRestore("restore")  (game-first: Local/Cloud -> systems -> games)
//    Settings -> ES-DE settings restore (a later phase) - for now a "coming soon" note
//    BIOS     -> pick a source (ON THIS DECK folder / MEGA CLOUD) -> GuiMadPageBios("restore", ...)
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RESTORE_HUB_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RESTORE_HUB_H

#include "guis/mad/MadPage.h"

#include <memory>
#include <string>
#include <vector>

class MadTileGrid;

class GuiMadPageRestoreHub : public MadPage
{
public:
    explicit GuiMadPageRestoreHub(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onSaveFocus() override;
    void onRestoreFocus() override;

private:
    void onPick(const std::string& key);

    std::shared_ptr<MadTileGrid> mGrid;
    int mGridCookie {0};
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RESTORE_HUB_H
