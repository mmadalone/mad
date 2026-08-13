//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEsde.h  (deck-patches)
//
//  Granular ES-DE SETTINGS backup & STAGED restore: the durable ROOT of an ES-DE-settings op. Opens on the
//  tickable GROUPS (esde.groups) - settings, custom collections, gamelists, scraper cache - with an
//  explanation side pane and the shared DESTINATION / SOURCE bar. Backup goes to granular.backup_esde or
//  cloud.push_esde.
//
//  Two things make this page different from its siblings, and both are house rules rather than taste:
//
//  1. The restore STAGES. ES-DE rewrites es_settings.xml and the gamelists on exit, so restoring them into
//     a running ES-DE would be clobbered (rule #3). granular.restore stages instead, and when the daemon
//     reports the write as deferred the page offers a one-tap restart to apply it.
//  2. Gamelists tick per SYSTEM, not as one lump. That group's row shows a half-tick with a count and A
//     opens the per-system drill-down (GuiMadPageEsdeGamelists), which ticks into mGamelistRels.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ESDE_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ESDE_H

#include "guis/mad/MadBackupGroupListPage.h"

#include <set>
#include <string>
#include <vector>

class GuiMadPageEsde : public MadBackupGroupListPage
{
public:
    // mode: "backup" (source="live", destination bar at the top) or "restore" (source bar at the top).
    GuiMadPageEsde(GuiMadPanel* panel, const std::string& mode);
    ~GuiMadPageEsde() override;

    // The gamelist drill (GuiMadPageEsdeGamelists) ticks per-system gamelist rels into this set.
    std::set<std::string>* gamelistSelection() { return &mGamelistRels; }

private:
    static bool isGamelists(const Group& g) { return g.key == "gamelists"; }

    // The gamelists group is ticked per system, so every tick question routes through the set.
    bool groupTicked(const Group& g) const override;
    std::string rowGlyph(const Group& g) const override;
    std::string rowTail(const Group& g) const override;
    bool onGroupActivated(Group& g) override;      // A on gamelists opens the drill-down
    void onGroupsLoaded(std::vector<Group>& groups) override;
    void onSourceReset() override { mGamelistRels.clear(); }
    void writeGroupItems(const Group& g, bool restore, MadJson::Writer& w) const override;

    // Staged restore: a different report, a different warning, a different explanation.
    void onRestoreSucceeded(const rapidjson::Value& data) override;
    std::string replaceWarningText(int replace) const override;
    std::string actionRowExplain() const override;

    void offerRestart(); // the wrapper-aware "RESTART ES-DE / LATER" dialog (staged restore)

    std::set<std::string> mGamelistRels; // ticked per-system gamelist rels (the gamelists group)
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ESDE_H
