//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageManageSets.h
//
//  Manage backups (deck-patches, P11): the set list for ONE category (or "all"). Each row is one backup SET -
//  local or MEGA - with its date, item count and size. A on a set PERMANENTLY deletes it (behind a hard
//  confirm); a leading "> Delete all ..." row bulk-deletes the whole category, or (in the "all" view) every
//  backup across every category. Deleting a BACKUP is a deliberate override of rule #5; primary data (games,
//  saves, config) is never touched. Backend: manage.list, then manage.delete_local / cloud.delete_set /
//  manage.delete_all.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_MANAGE_SETS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_MANAGE_SETS_H

#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadVirtualList.h"

#include <string>
#include <vector>

class TextComponent;

class GuiMadPageManageSets : public MadPage
{
public:
    // category: a granular category key (games/emucfg/esde/bios/system) or "all"; label = the display name.
    GuiMadPageManageSets(GuiMadPanel* panel, const std::string& category, const std::string& label);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    bool consumesSectionNav() override { return false; }
    void pageScroll(int direction) override;
    void onSaveFocus() override;
    void onRestoreFocus() override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct Set {
        std::string scope;    // "local" | "cloud"
        std::string category; // games/emucfg/esde/bios/system
        std::string id;       // local: absolute path; cloud: "cloud:<token>"
        std::string token;    // cloud only: the set token (games/bios/<ts>)
        std::string created;  // "YYYYmmddTHHMMSS" or ""
        long long size;       // bytes (0 for cloud)
        int count;
    };

    void fetchSets();
    void populate();
    bool hasDeleteAllRow() const { return !mSets.empty(); }
    std::string headerText() const;
    std::string rowText(const Set& s) const;
    std::string setDesc(const Set& s) const;

    void activateAt(int i);        // A: row 0 = the "delete all" row (if present); else a set
    void confirmDeleteOne(const Set& s);
    void confirmDeleteAll();
    void doDeleteOne(const Set& s);
    void doDeleteAll();

    std::string mCategory;         // "all" or a category key
    std::string mLabel;
    std::vector<Set> mSets;
    bool mBusy {false};            // a delete RPC is in flight (guards a double A)

    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    int mFocusCookie {0};
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_MANAGE_SETS_H
