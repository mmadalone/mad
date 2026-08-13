//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageControllers.cpp  (deck-patches, P12b Build B)
//

#include "guis/mad/pages/GuiMadPageControllers.h"

#include "Window.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadTheme.h"

GuiMadPageControllers::GuiMadPageControllers(GuiMadPanel* panel, const std::string& mode)
    : MadBackupGroupListPage {panel,
                              mode == "restore" ? "RESTORE CONTROLLER CONFIG"
                                                : "BACK UP CONTROLLER CONFIG",
                              mode,
                              Params {/*category=*/"controllers",
                                      /*cloudSourcesRpc=*/"controllers.cloud_sources",
                                      /*backupRpc=*/"granular.backup_controllers",
                                      /*cloudPushRpc=*/"cloud.push_controllers",
                                      /*contentRpc=*/"controllers.groups",
                                      /*noun=*/"controller config",
                                      /*sourceNoun=*/"controller-config",
                                      /*itemNoun=*/" item(s)",
                                      /*restoreStatus=*/"Restoring controller config..."}}
{
}

GuiMadPageControllers::~GuiMadPageControllers()
{
    clearRunStream();
}

// ── the two local revert ops (restore mode only) ─────────────────────────────

MadVirtualList::Row GuiMadPageControllers::extraRow(int index) const
{
    return {index == 0 ? "> Revert emulator inputs to pre-MAD backups"
                       : "> Reset routing overrides to defaults",
            MadTheme::color(MadColor::Red)};
}

std::string GuiMadPageControllers::extraRowExplain(int index) const
{
    if (index == 0)
        return "Revert EVERY emulator input config to the one-time backup taken before MAD's first "
               "write (the pre-MAD state). Close any open emulators first.";
    return "Delete your MAD routing overrides (controller-policy.local.toml) and go back to the "
           "documented defaults.";
}

void GuiMadPageControllers::onExtraRow(int index)
{
    if (index == 0)
        revertInputBackups();
    else
        resetOverrides();
}

void GuiMadPageControllers::revertInputBackups()
{
    if (mRunning) {
        footer()->flash("A backup or restore is already running - let it finish first.", 4000, true);
        return;
    }
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new MadMsgBox(
        "Revert EVERY emulator input config to its one-time backup from before MAD's first write? Close any "
        "open emulators first. A recoverable copy is kept.",
        "REVERT",
        [this, alive] {
            if (alive.expired())
                return;
            footer()->setStatus("Reverting input configs...");
            std::weak_ptr<int> a2 {pageAlive()};
            pageRequest("backup.restore_router", nullptr,
                        [this, a2](bool ok, const rapidjson::Value& p) {
                            if (a2.expired())
                                return;
                            footer()->setStatus("");
                            footer()->flash(ok ? MadJson::getString(p, "message", "Input configs reverted.")
                                               : "Couldn't revert: " +
                                                     MadJson::getString(p, "message", "error"),
                                            4500, !ok);
                        },
                        30000);
        },
        "CANCEL", nullptr));
}

void GuiMadPageControllers::resetOverrides()
{
    if (mRunning) {
        footer()->flash("A backup or restore is already running - let it finish first.", 4000, true);
        return;
    }
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new MadMsgBox(
        "Delete ALL your MAD routing overrides (controller-policy.local.toml) and revert to the documented "
        "defaults?",
        "RESET",
        [this, alive] {
            if (alive.expired())
                return;
            std::weak_ptr<int> a2 {pageAlive()};
            pageRequest("backup.reset_local", nullptr,
                        [this, a2](bool ok, const rapidjson::Value& p) {
                            if (a2.expired())
                                return;
                            footer()->flash(ok ? MadJson::getString(p, "message", "Reset to defaults.")
                                               : "Couldn't reset: " +
                                                     MadJson::getString(p, "message", "error"),
                                            4500, !ok);
                        });
        },
        "CANCEL", nullptr));
}
