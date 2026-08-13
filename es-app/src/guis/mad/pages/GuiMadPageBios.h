//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBios.h
//
//  Granular BIOS backup & restore (deck-patches): the durable ROOT of a BIOS op. Opens straight on the
//  per-system BIOS bucket tiles with a DESTINATION / SOURCE bar across the top (X toggles On-this-Deck <->
//  MEGA; Y changes the folder on backup, or picks a different backup on restore). Drills into ONE bucket's
//  file list (GuiMadPageBiosFiles) and OWNS the running backup/restore so a popped file list never orphans
//  it. Backup: X on the file list backs up to the bar's destination (a local folder -> granular.backup_bios,
//  or MEGA -> cloud.push_bios). Restore: X restores over the live BIOS from the bar's source (a local backup
//  folder or "cloud:<ts>"), rule-5 warned. Cloud BIOS uses its OWN remote base (bios-backups).
//
//  The flow itself is MadBackupTilePage's. All this page adds is its category and one art rule.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H

#include "guis/mad/MadBackupTilePage.h"

#include <string>

class GuiMadPageBios : public MadBackupTilePage
{
public:
    // mode: "backup" (source="live", destination bar at the top) or "restore" (source bar at the top).
    GuiMadPageBios(GuiMadPanel* panel, const std::string& mode);
    ~GuiMadPageBios() override;

private:
    // BIOS files that belong to no console land in an "other" bucket with its own icon.
    std::string tileArt(const Item& i) const override;
    MadPage* makeLeafPage(const std::string& key, const std::string& label) override;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H
