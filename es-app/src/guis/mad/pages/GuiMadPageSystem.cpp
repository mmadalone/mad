//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageSystem.cpp  (deck-patches, P12a)
//

#include "guis/mad/pages/GuiMadPageSystem.h"

GuiMadPageSystem::GuiMadPageSystem(GuiMadPanel* panel, const std::string& mode)
    : MadBackupGroupListPage {panel,
                              mode == "restore" ? "RESTORE SYSTEM CONFIG" : "BACK UP SYSTEM CONFIG",
                              mode,
                              Params {/*category=*/"system",
                                      /*cloudSourcesRpc=*/"system.cloud_sources",
                                      /*backupRpc=*/"granular.backup_system",
                                      /*cloudPushRpc=*/"cloud.push_system",
                                      /*contentRpc=*/"system.groups",
                                      /*noun=*/"system config",
                                      /*sourceNoun=*/"system-config",
                                      /*itemNoun=*/" item(s)",
                                      /*listNoun=*/"",
                                      /*restoreStatus=*/"Restoring system config..."}}
{
}

GuiMadPageSystem::~GuiMadPageSystem()
{
    clearRunStream();
}
