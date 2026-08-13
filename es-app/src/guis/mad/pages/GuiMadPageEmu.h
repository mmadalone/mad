//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEmu.h  (deck-patches, P7)
//
//  Granular EMULATOR CONFIG backup & restore: the durable ROOT of an emulator-config op. Opens on the
//  per-emulator tiles with the shared DESTINATION / SOURCE bar, drills into ONE emulator's group list
//  (GuiMadPageEmuFiles - Config / Controller / Per-game / Keys / Saves / Textures) and OWNS the running
//  op so a popped leaf never orphans it. Backup: granular.backup_emucfg or cloud.push_emucfg. Restore:
//  granular.restore category="emucfg", rule-5 warned.
//
//  The flow itself is MadBackupTilePage's. What this page adds is the two places emulator config talks
//  differently: a restore that cannot start says WHY in the emulator's own words (the per-emulator EBUSY
//  guard), and a restore that succeeded says when it takes effect.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_H

#include "guis/mad/MadBackupTilePage.h"

#include <string>

class GuiMadPageEmu : public MadBackupTilePage
{
public:
    // mode: "backup" (source="live", destination bar at the top) or "restore" (source bar at the top).
    GuiMadPageEmu(GuiMadPanel* panel, const std::string& mode);
    ~GuiMadPageEmu() override;

private:
    MadPage* makeLeafPage(const std::string& key, const std::string& label) override;
    // The backend's message IS the answer here ("close PCSX2 before restoring its config"), so it is
    // shown verbatim rather than prefixed, and for longer.
    std::string restoreStartErrorText(const std::string& message) const override { return message; }
    int restoreStartErrorMs() const override { return 6000; }
    std::string restoreDoneSuffix() const override
    {
        return "The emulator picks it up on its next launch.";
    }
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_H
