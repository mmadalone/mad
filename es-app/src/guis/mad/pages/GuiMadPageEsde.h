//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEsde.h
//
//  Granular ES-DE settings backup & staged restore (deck-patches, P6): the durable ROOT of an ES-DE
//  settings op. Opens on the 5 tickable GROUPS (esde.groups) - Main settings / Controller input / Custom
//  systems / Collections / Game favorites & metadata - with a plain-English explanation side pane and a
//  DESTINATION / SOURCE bar across the top (X toggles On-this-Deck <-> MEGA; Y changes the folder on backup,
//  or picks a different dated backup on restore - the same location flow as Games / BIOS). A on the last
//  "Back up / Restore now" row runs the op; A on the "Game favorites & metadata" row drills per-system
//  (GuiMadPageEsdeGamelists). Backup: to a local folder -> granular.backup_esde, or MEGA -> cloud.push_esde.
//  Restore (source = a dated backup or "cloud:<ts>"): STAGES the restore (granular.restore category="esde"
//  -> next-boot apply, rule #3) and offers a RESTART. Leaving mid-op is allowed (the daemon op keeps
//  running); a cloud backup reattaches to the Landing Transfers tile.
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
    // mode: "backup" (source="live", destination bar at the top) or "restore" (source bar at the top).
    GuiMadPageEsde(GuiMadPanel* panel, const std::string& mode);
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
    void onListSelect(int listIndex);   // list row 0 = act; rows 1.. = the groups (A ticks / opens the drill)
    void toggleAt(int groupIndex);
    void openGamelistDrill();     // A on the gamelists group row
    void act();                   // A on the top "Back up / Restore now" row

    // the destination (backup) / source (restore) bar at the top (X toggles MEGA, Y changes the folder /
    // picks a different dated backup - the same location flow as Games / BIOS).
    void ensureBar();
    void refreshBar();
    std::string barText() const;
    void toggleCloud();
    void changeTarget();
    void resolveDefaultSource();
    void openSourcePicker();
    void applySource(const std::string& id, const std::string& created, int count);
    void browseForSource();

    // backup: fire straight to the bar's destination.
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

    // dest/source bar state (same as Games/BIOS).
    bool mCloud {false};          // the destination/source is MEGA
    std::string mDest;            // backup: the remembered local destination folder
    std::string mSrcCreated;      // restore: the current source's timestamp
    int mSrcCount {0};            // restore: the current source's file count
    bool mHasSource {false};
    bool mChecking {false};
    int mSrcGen {0};              // bumped when the source changes; a stale in-flight resolve bails
    std::shared_ptr<TextComponent> mBar;

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
