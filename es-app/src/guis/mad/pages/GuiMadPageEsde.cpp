//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEsde.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageEsde.h"

#include "Window.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadPageUtil.h"
#include "guis/mad/pages/GuiMadPageEsdeGamelists.h"
#include "utils/PlatformUtil.h"

#include <cstdlib>

GuiMadPageEsde::GuiMadPageEsde(GuiMadPanel* panel, const std::string& mode)
    : MadBackupGroupListPage {panel,
                              mode == "restore" ? "RESTORE ES-DE SETTINGS" : "BACK UP ES-DE SETTINGS",
                              mode,
                              Params {/*category=*/"esde",
                                      /*cloudSourcesRpc=*/"esde.cloud_sources",
                                      /*backupRpc=*/"granular.backup_esde",
                                      /*cloudPushRpc=*/"cloud.push_esde",
                                      /*contentRpc=*/"esde.groups",
                                      /*noun=*/"ES-DE settings",
                                      /*sourceNoun=*/"ES-DE settings",
                                      /*itemNoun=*/" ES-DE setting(s)",
                                      /*restoreStatus=*/"Staging ES-DE settings..."}}
{
}

GuiMadPageEsde::~GuiMadPageEsde()
{
    clearRunStream();
}

// ── the gamelists group: ticked per system ───────────────────────────────────

bool GuiMadPageEsde::groupTicked(const Group& g) const
{
    if (isGamelists(g)) {
        for (const File& f : g.files)
            if (mGamelistRels.count(f.rel))
                return true;
        return false;
    }
    return g.selected;
}

std::string GuiMadPageEsde::rowGlyph(const Group& g) const
{
    if (!isGamelists(g))
        return g.selected ? "● " : "○ ";
    int ticked {0};
    for (const File& f : g.files)
        if (mGamelistRels.count(f.rel))
            ++ticked;
    // A partial pick gets its own glyph: "some systems" is a real state here, not on/off.
    return ticked == 0 ? "○ " : ticked == static_cast<int>(g.files.size()) ? "● " : "◐ ";
}

std::string GuiMadPageEsde::rowTail(const Group& g) const
{
    if (!isGamelists(g))
        return MadBackupGroupListPage::rowTail(g);
    int ticked {0};
    for (const File& f : g.files)
        if (mGamelistRels.count(f.rel))
            ++ticked;
    return "  (" + std::to_string(ticked) + " of " + std::to_string(g.files.size()) + " systems)";
}

bool GuiMadPageEsde::onGroupActivated(Group& g)
{
    if (!isGamelists(g))
        return false;
    if (mRunning)
        return true;
    mPanel->pushPage(new GuiMadPageEsdeGamelists(mPanel, this, mSource));
    return true;
}

void GuiMadPageEsde::onGroupsLoaded(std::vector<Group>& groups)
{
    mGamelistRels.clear();
    for (Group& g : groups) {
        if (!isGamelists(g))
            continue;
        // The gamelists group is never a plain tick; its state lives in the set, pre-filled with
        // every system so "restore everything" stays one press.
        g.selected = false;
        if (g.present)
            for (const File& f : g.files)
                mGamelistRels.insert(f.rel);
    }
}

void GuiMadPageEsde::writeGroupItems(const Group& g, bool restore, MadJson::Writer& w) const
{
    if (!isGamelists(g)) {
        MadBackupGroupListPage::writeGroupItems(g, restore, w);
        return;
    }
    for (const File& f : g.files) {
        if (!mGamelistRels.count(f.rel))
            continue;
        w.StartObject();
        if (restore) {
            w.Key("system");
            w.String(g.key.c_str(), static_cast<rapidjson::SizeType>(g.key.length()));
            w.Key("id");
        }
        else {
            w.Key("group");
            w.String(g.key.c_str(), static_cast<rapidjson::SizeType>(g.key.length()));
            w.Key("rel");
        }
        w.String(f.rel.c_str(), static_cast<rapidjson::SizeType>(f.rel.length()));
        w.EndObject();
    }
}

// ── staged restore ───────────────────────────────────────────────────────────

std::string GuiMadPageEsde::actionRowExplain() const
{
    return mBackup
        ? "Press A to back up the ticked groups to the destination in the bar above."
        : "Press A to restore the ticked groups. ES-DE settings apply the next time ES-DE starts.";
}

std::string GuiMadPageEsde::replaceWarningText(int replace) const
{
    return std::to_string(replace) +
           " ES-DE setting(s) on disk will be REPLACED when ES-DE restarts. A recoverable copy is "
           "saved aside first. Continue?";
}

void GuiMadPageEsde::onRestoreSucceeded(const rapidjson::Value& data)
{
    // Nothing was written live, so there is no replaced/skipped/orphaned tally to report - the
    // files land on the next start.
    const int restored {MadJson::getInt(data, "restored", 0)};
    footer()->flash("Staged " + std::to_string(restored) + " ES-DE setting(s) for the next start.",
                    9000, false);
    if (MadJson::getBool(data, "deferred"))
        offerRestart();
}

void GuiMadPageEsde::offerRestart()
{
    // Staged config applies on the NEXT start; offer a one-tap restart (wrapper-aware, like the precious
    // restore). RESTART re-execs $MAD_WRAPPER in place; without it, a QUIT applies on the next manual launch.
    const bool madRestart {std::getenv("MAD_WRAPPER") != nullptr};
    mWindow->pushGui(new MadMsgBox(
        "Your ES-DE settings are staged and apply the next time ES-DE starts.\n\nRestart ES-DE now to "
        "apply them?",
        madRestart ? "RESTART ES-DE" : "QUIT ES-DE",
        [madRestart] {
            Utils::Platform::quitES(madRestart ? Utils::Platform::QuitMode::RESTART
                                               : Utils::Platform::QuitMode::QUIT);
        },
        "LATER", [] {}));
}
