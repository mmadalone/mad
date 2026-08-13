//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEmu.cpp  (deck-patches, P7)
//

#include "guis/mad/pages/GuiMadPageEmu.h"

#include "guis/mad/pages/GuiMadPageEmuFiles.h"

GuiMadPageEmu::GuiMadPageEmu(GuiMadPanel* panel, const std::string& mode)
    : MadBackupTilePage {panel,
                         mode == "restore" ? "RESTORE EMULATOR CONFIG" : "BACK UP EMULATOR CONFIG",
                         mode,
                         Params {/*category=*/"emucfg",
                                 /*cloudSourcesRpc=*/"emucfg.cloud_sources",
                                 /*backupRpc=*/"granular.backup_emucfg",
                                 /*cloudPushRpc=*/"cloud.push_emucfg",
                                 /*contentRpc=*/"emucfg.systems",
                                 /*noun=*/"emulator config",
                                 /*sourceNoun=*/"emulator-config",
                                 /*itemNoun=*/" item(s)",
                                 /*restoreStatus=*/"Restoring emulator config..."},
                         TileParams {/*itemKeyName=*/"emulator",
                                     /*backupAllRpc=*/"granular.backup_emucfg_all",
                                     /*cloudPushAllRpc=*/"cloud.push_emucfg_all",
                                     /*categoryIcon=*/"backup-emu",
                                     /*allConfirmNoun=*/"ALL emulator config",
                                     /*allConfirmExtra=*/", includes texture/mod packs",
                                     /*allDurationWord=*/"a long time",
                                     /*emptyBackup=*/"No emulators with config found.",
                                     /*emptyRestore=*/"This backup has no emulator config."}}
{
}

GuiMadPageEmu::~GuiMadPageEmu()
{
    clearRunStream();
}

MadPage* GuiMadPageEmu::makeLeafPage(const std::string& key, const std::string& label)
{
    return new GuiMadPageEmuFiles(mPanel, this, mSource, key, label, !mBackup);
}
