//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEsde.h
//
//  Granular ES-DE settings backup & staged restore (deck-patches, P6): the durable ROOT of an ES-DE
//  settings op. Shows the 5 tickable GROUPS (esde.groups) - Main settings / Controller input / Custom
//  systems / Collections / Game favorites & metadata - with a plain-English explanation side pane. The
//  gamelists group drills per-system (GuiMadPageEsdeGamelists). Backup mode (source="live"): X opens a
//  destination chooser (ON THIS DECK -> granular.backup_esde / MEGA CLOUD -> cloud.push_esde). Restore mode
//  (source = a backup folder, "cloud:<ts>", or the "cloud" sentinel to pick a MEGA set): X STAGES the
//  restore (granular.restore category="esde" -> next-boot apply, rule #3) and offers a RESTART. Leaving
//  mid-op is allowed (the daemon op keeps running); a cloud backup reattaches to the Landing Transfers tile.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ESDE_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ESDE_H

#include "guis/mad/MadPage.h"

#include <memory>
#include <set>
#include <string>
#include <vector>

class MadVirtualList;
class TextComponent;

class GuiMadPageEsde : public MadPage
{
public:
    // mode: "backup" (source="live") or "restore" (source = a backup folder, "cloud:<ts>", or the "cloud"
    // sentinel = pick a MEGA set from the cloud source list first).
    GuiMadPageEsde(GuiMadPanel* panel, const std::string& mode, const std::string& source);
    ~GuiMadPageEsde() override;

    void build() override;
    void update(int deltaTime) override;
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
    enum class Pending { None, ShowGroups };
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
    struct Src {
        std::string id;
        std::string created;
        int count;
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

    // backup destination chooser (mirrors GuiMadPageBios).
    void startBackupChooser();
    void beginBackupLocal(const std::string& dest);
    void beginBackupCloud();
    // staged restore: preview -> replace-warning -> granular.restore -> RESTART/LATER on the terminal.
    void startRestore();

    // restore-mode cloud source list (only when the sentinel source "cloud" was passed) - clone of bios.
    void showCloudSourceList();
    void ensureSourceList();
    void hideSourceList();
    void rebuildSourceList();
    void fetchCloudSources();
    void onPickSource(int index);
    static std::string fmtSourceLabel(const std::string& created, int count);

    void writeItems(MadJson::Writer& w, bool restore) const; // the ticked files as items[]
    bool anyTicked() const;
    void attachRunStream(const std::string& token, bool restore, bool cloud);
    void clearRunStream();
    void offerRestart();          // the wrapper-aware "RESTART ES-DE / LATER" dialog (staged restore)

    std::string mMode;            // "backup" | "restore"
    std::string mSource;          // "live" | a backup folder | "cloud:<ts>" | "cloud"
    bool mBackup;

    std::vector<Group> mGroups;
    std::set<std::string> mGamelistRels; // ticked per-system gamelist rels (the gamelists group)
    Pending mPending {Pending::None};

    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    std::shared_ptr<TextComponent> mExplain;

    // cloud source list (restore, sentinel "cloud"); parallel mSourceRowId: "cloud:<ts>" or "" for a note.
    bool mPickingSource {false};
    std::shared_ptr<MadVirtualList> mSourceList;
    std::vector<Src> mCloudSrc;
    std::vector<std::string> mSourceRowId;
    bool mCloudLoaded {false};
    bool mCloudLoading {false};
    bool mCloudConnected {false};
    int mSourceCookie {0};

    bool mRunning {false};
    bool mRestorePreviewing {false};
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ESDE_H
