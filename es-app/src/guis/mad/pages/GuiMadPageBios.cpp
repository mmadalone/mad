//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBios.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageBios.h"

#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBiosFiles.h"

GuiMadPageBios::GuiMadPageBios(GuiMadPanel* panel, const std::string& mode)
    : MadBackupTilePage {panel,
                         mode == "restore" ? "RESTORE BIOS" : "BACK UP BIOS",
                         mode,
                         Params {/*category=*/"bios",
                                 /*cloudSourcesRpc=*/"bios.cloud_sources",
                                 /*backupRpc=*/"granular.backup_bios",
                                 /*cloudPushRpc=*/"cloud.push_bios",
                                 /*contentRpc=*/"bios.systems",
                                 /*noun=*/"BIOS",
                                 /*sourceNoun=*/"BIOS",
                                 /*itemNoun=*/" BIOS file(s)",
                                 /*restoreStatus=*/"Restoring BIOS..."},
                         TileParams {/*itemKeyName=*/"bucket",
                                     /*backupAllRpc=*/"granular.backup_bios_all",
                                     /*cloudPushAllRpc=*/"cloud.push_bios_all",
                                     /*categoryIcon=*/"backup-bios",
                                     /*allConfirmNoun=*/"ALL BIOS",
                                     /*allConfirmExtra=*/"",
                                     /*allDurationWord=*/"a while",
                                     /*emptyBackup=*/"No BIOS files found.",
                                     /*emptyRestore=*/"This backup has no BIOS."}}
{
}

GuiMadPageBios::~GuiMadPageBios()
{
    clearRunStream();
}

std::string GuiMadPageBios::tileArt(const Item& i) const
{
    // reuse the console art the backend resolved; else the dedicated "Other" icon, else the BIOS icon.
    if (!i.art.empty())
        return i.art;
    if (i.key == "other")
        return MadTheme::routerIconPath("backup-other-bios");
    return MadBackupTilePage::tileArt(i);
}

MadPage* GuiMadPageBios::makeLeafPage(const std::string& key, const std::string& label)
{
    return new GuiMadPageBiosFiles(mPanel, this, mSource, key, label, !mBackup);
}
