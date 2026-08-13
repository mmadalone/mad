//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageControllers.h  (deck-patches, P12b Build B)
//
//  Granular CONTROLLER config backup & LIVE restore: the durable ROOT of a controller-config op. Opens on
//  the tickable GROUPS (controllers.groups) - MAD routing / emulator input configs / Steam Input / udev -
//  with an explanation side pane and the shared DESTINATION / SOURCE bar (X toggles On-this-Deck <-> MEGA,
//  Y changes the folder on backup or picks a different dated backup on restore). Backup goes to
//  granular.backup_controllers or cloud.push_controllers; restore is a LIVE restore
//  (granular.restore category="controllers").
//
//  What this page adds to the shared flow: in RESTORE mode two LOCAL revert ops sit below the action row,
//  kept from the old Controller config page. They are not tied to a backup source - one reverts every
//  emulator input config to its one-time pre-MAD backup, the other resets the routing overrides to
//  defaults - so they arrive as extra rows rather than as ticks.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_CONTROLLERS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_CONTROLLERS_H

#include "guis/mad/MadBackupGroupListPage.h"

#include <string>

class GuiMadPageControllers : public MadBackupGroupListPage
{
public:
    // mode: "backup" (source="live", destination bar at the top) or "restore" (source bar at the top).
    GuiMadPageControllers(GuiMadPanel* panel, const std::string& mode);
    ~GuiMadPageControllers() override;

private:
    // The two revert ops, restore mode only.
    int extraRowCount() const override { return mBackup ? 0 : 2; }
    MadVirtualList::Row extraRow(int index) const override;
    std::string extraRowExplain(int index) const override;
    void onExtraRow(int index) override;

    void revertInputBackups(); // backup.restore_router
    void resetOverrides();     // backup.reset_local
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_CONTROLLERS_H
