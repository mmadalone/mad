//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageSystem.h
//
//  Granular SYSTEM config backup & LIVE restore (deck-patches, P12a): the durable ROOT of a System-config
//  op. Opens on the tickable GROUPS (system.groups) - Control panel & calibration / Lightgun / Samba /
//  Backup settings / EmuDeck settings - with a plain-English explanation side pane and a DESTINATION /
//  SOURCE bar across the top (X toggles On-this-Deck <-> MEGA; Y changes the folder on backup, or picks a
//  different dated backup on restore - the same location flow as Games / BIOS / ES-DE settings). A on the
//  "Back up / Restore now" row runs the op. Backup: to a local folder -> granular.backup_system, or
//  MEGA -> cloud.push_system. Restore (source = a dated backup or "cloud:<ts>"): a LIVE restore
//  (granular.restore category="system") under rule #5 - system config is read by the control panel/helpers,
//  never rewritten by ES-DE, so no staging/restart is needed. Leaving mid-op is allowed (the daemon op keeps
//  running); a cloud backup reattaches to the Landing Transfers tile.
//
//  All of that behaviour is MadBackupGroupListPage's; this page is the category it runs on. It overrides
//  no hooks at all, which makes it the reference for what the shared flow does by default.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SYSTEM_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SYSTEM_H

#include "guis/mad/MadBackupGroupListPage.h"

#include <string>

class GuiMadPageSystem : public MadBackupGroupListPage
{
public:
    // mode: "backup" (source="live", destination bar at the top) or "restore" (source bar at the top).
    GuiMadPageSystem(GuiMadPanel* panel, const std::string& mode);
    ~GuiMadPageSystem() override;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SYSTEM_H
