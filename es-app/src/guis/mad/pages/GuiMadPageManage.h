//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageManage.h
//
//  Manage backups (deck-patches, P11): the category hub reached from the Backup page's "Manage backups"
//  tile. A tile grid - a leading "All" tile + one per granular category (Games / Emulator config / ES-DE
//  settings / BIOS / System), each showing how many backup SETS it has (local + MEGA). Picking a tile opens
//  that category's set list (GuiMadPageManageSets) where an individual set - or, via a "Delete all" row, the
//  whole category / everything - can be PERMANENTLY deleted (a deliberate override of rule #5 for a BACKUP
//  copy; primary data is never touched). It owns NO running op (deletes are single RPCs), so it is a plain
//  tile page; on return it re-reads the counts so a deletion is reflected.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_MANAGE_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_MANAGE_H

#include "guis/mad/MadPage.h"

#include <memory>
#include <string>
#include <vector>

class MadTileGrid;

class GuiMadPageManage : public MadPage
{
public:
    explicit GuiMadPageManage(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onSaveFocus() override;
    void onRestoreFocus() override;

private:
    void fetchCounts();
    void rebuild();
    void onPick(const std::string& key);

    // per-category set counts, indexed to the 5 categories in kCats order (see .cpp); mTotal = all of them.
    std::vector<int> mCounts;
    int mTotal {0};
    bool mLoaded {false};

    std::shared_ptr<MadTileGrid> mGrid;
    int mGridCookie {0};
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_MANAGE_H
