//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadBackupGroupListPage.h
//
//  MAD control panel: the group-list half of the backup flow (deck-patches).
//  System config, Controller config and ES-DE settings all present their
//  category the same way - a tickable list of groups with a plain-English
//  explanation pane beside it and an action row underneath - so that list,
//  its ticking rules and the backup / restore starters live here once.
//
//  The three pages differ only in the hooks below: Controller config appends
//  two local revert ops under the action row, and ES-DE settings ticks
//  gamelists per system and stages its restore instead of applying it.
//

#ifndef ES_APP_GUIS_MAD_MAD_BACKUP_GROUP_LIST_PAGE_H
#define ES_APP_GUIS_MAD_MAD_BACKUP_GROUP_LIST_PAGE_H

#include "guis/mad/MadBackupFlowPage.h"
#include "guis/mad/MadBackupRowMap.h"
#include "guis/mad/widgets/MadVirtualList.h"

#include <memory>
#include <string>
#include <vector>

class TextComponent;

class MadBackupGroupListPage : public MadBackupFlowPage
{
public:
    bool input(InputConfig* config, Input input) override;
    bool onBackPressed() override;
    bool consumesSectionNav() override { return false; } // leaving mid-op is allowed (the daemon runs it)
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onSaveFocus() override;
    void onRestoreFocus() override;

protected:
    struct File {
        std::string rel;
        std::string name;
        long long size;
    };
    struct Group {
        std::string key;
        std::string label;
        std::string explain;
        std::vector<File> files;
        long long size;
        bool present;
        bool selected;
    };

    MadBackupGroupListPage(GuiMadPanel* panel, const std::string& title, const std::string& mode,
                           const Params& params);

    void build() override;
    void fetchGroups();
    void rebuildGroups();

    // ── hooks ────────────────────────────────────────────────────────────────
    // Is this group ticked? ES-DE settings answers from its per-system gamelist set instead of
    // the plain flag. Everything that counts, colours or writes ticks asks THIS, never g.selected.
    virtual bool groupTicked(const Group& g) const { return g.selected; }
    virtual std::string rowGlyph(const Group& g) const { return groupTicked(g) ? "● " : "○ "; }
    virtual std::string rowTail(const Group& g) const;
    // A on a group. Return true when the page consumed it (ES-DE settings opens its gamelist
    // drill-down instead of toggling).
    virtual bool onGroupActivated(Group& g) { return false; }
    // Last chance to adjust the freshly parsed groups (ES-DE settings unticks gamelists and
    // pre-fills its own set from them).
    virtual void onGroupsLoaded(std::vector<Group>& groups) {}
    // A new source is being resolved; drop any per-page selection derived from the old one.
    virtual void onSourceReset() {}
    // Emit the items[] entries for one ticked group. The default sends every file it holds.
    virtual void writeGroupItems(const Group& g, bool restore, MadJson::Writer& w) const;
    // Extra action rows BELOW the action row (Controller config's two revert ops).
    virtual int extraRowCount() const { return 0; }
    virtual MadVirtualList::Row extraRow(int index) const { return {}; }
    virtual std::string extraRowExplain(int index) const { return ""; }
    virtual void onExtraRow(int index) {}
    // The replace warning shown before a restore that would overwrite live files.
    virtual std::string replaceWarningText(int replace) const;
    // The explanation shown while the cursor sits on the action row. ES-DE settings says something
    // else because its restore stages rather than applies.
    virtual std::string actionRowExplain() const;

    // MadBackupFlowPage
    void refetchForSource() override;
    void teardownContent() override;

    std::vector<Group> mGroups;
    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    std::shared_ptr<TextComponent> mExplain;

private:
    void ensureWidgets();
    void updateExplain();
    std::string headerText() const;
    std::string rowText(const Group& g) const;
    // Rows 0..n-1 are the groups, row n is the action, rows above that are extraRow()s.
    void onListSelect(int listIndex);
    void toggleAt(int groupIndex);
    void act();
    bool anyTicked() const;
    void writeItems(MadJson::Writer& w, bool restore) const;
    void beginBackupLocal(const std::string& dest);
    void beginBackupCloud();
    void startRestore();
};

#endif // ES_APP_GUIS_MAD_MAD_BACKUP_GROUP_LIST_PAGE_H
