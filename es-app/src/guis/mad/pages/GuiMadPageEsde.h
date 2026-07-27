//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEsde.h
//
//  Granular ES-DE settings backup & staged restore (deck-patches, P6): the durable ROOT of an ES-DE
//  settings op. Reached AFTER the destination/source is chosen (GuiMadPageChooseTarget), so it opens
//  straight on the 5 tickable GROUPS (esde.groups) - Main settings / Controller input / Custom systems /
//  Collections / Game favorites & metadata - with a plain-English explanation side pane. The gamelists
//  group drills per-system (GuiMadPageEsdeGamelists). Backup mode (source="live"): X backs up to the
//  resolved destination (a local folder -> granular.backup_esde, or MEGA -> cloud.push_esde). Restore mode
//  (source = a backup folder or "cloud:<ts>"): X STAGES the restore (granular.restore category="esde" ->
//  next-boot apply, rule #3) and offers a RESTART. Leaving mid-op is allowed (the daemon op keeps running);
//  a cloud backup reattaches to the Landing Transfers tile.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ESDE_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ESDE_H

#include "guis/mad/MadPage.h"
#include "guis/mad/pages/GuiMadPageChooseTarget.h" // MadTarget (the resolved destination / source)

#include <memory>
#include <set>
#include <string>
#include <vector>

class MadVirtualList;
class TextComponent;

class GuiMadPageEsde : public MadPage
{
public:
    // mode: "backup" (source="live", backs up to `target`) or "restore" (source = target.source, a backup
    // folder or "cloud:<ts>").
    GuiMadPageEsde(GuiMadPanel* panel, const std::string& mode, const MadTarget& target);
    ~GuiMadPageEsde() override;

    void build() override;
    bool input(InputConfig* config, Input input) override;
    bool onBackPressed() override;
    bool consumesSectionNav() override { return false; } // leaving mid-op is allowed (op runs on the daemon)
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onSaveFocus() override;
    void onRestoreFocus() override;

    bool busy() const { return mRunning; }
    // The gamelist drill (GuiMadPageEsdeGamelists) ticks per-system gamelist rels into this set.
    std::set<std::string>* gamelistSelection() { return &mGamelistRels; }

private:
    struct File {
        std::string rel;
        std::string name;
        long long size;
    };
    struct Group {
        std::string key;
        std::string label;
        std::string explain;
        std::vector<File> files;
        long long size;
        bool present;
        bool selected;            // simple groups; the gamelists group derives its tick from mGamelistRels
    };

    static bool isGamelists(const Group& g) { return g.key == "gamelists"; }
    bool groupTicked(const Group& g) const;

    void fetchGroups();
    void ensureWidgets();
    void rebuildGroups();
    void updateExplain();
    std::string headerText() const;
    std::string rowText(const Group& g) const;
    void toggleAt(int i);
    void openGamelistDrill();     // Y on the gamelists group
    void act();                   // X: back up / restore the ticked groups

    // backup: fire straight to the resolved destination (no chooser here - it ran upstream).
    void beginBackupLocal(const std::string& dest);
    void beginBackupCloud();
    // staged restore: preview -> replace-warning -> granular.restore -> RESTART/LATER on the terminal.
    void startRestore();

    void writeItems(MadJson::Writer& w, bool restore) const; // the ticked files as items[]
    bool anyTicked() const;
    void attachRunStream(const std::string& token, bool restore, bool cloud);
    void clearRunStream();
    void offerRestart();          // the wrapper-aware "RESTART ES-DE / LATER" dialog (staged restore)

    std::string mMode;            // "backup" | "restore"
    std::string mSource;          // "live" | a backup folder | "cloud:<ts>"
    bool mBackup;
    bool mCloud;                  // backup: the destination is MEGA (else mDest is a local folder)
    std::string mDest;            // backup: the resolved local destination folder ("" when mCloud)

    std::vector<Group> mGroups;
    std::set<std::string> mGamelistRels; // ticked per-system gamelist rels (the gamelists group)

    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    std::shared_ptr<TextComponent> mExplain;

    bool mRunning {false};
    bool mRestorePreviewing {false};
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ESDE_H
