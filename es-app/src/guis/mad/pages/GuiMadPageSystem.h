//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageSystem.h
//
//  Granular SYSTEM config backup & LIVE restore (deck-patches, P12a): the durable ROOT of a System-config
//  op. Opens on the tickable GROUPS (system.groups) - Control panel & calibration / Lightgun / Samba /
//  Backup settings / EmuDeck settings - with a plain-English explanation side pane and a DESTINATION /
//  SOURCE bar across the top (X toggles On-this-Deck <-> MEGA; Y changes the folder on backup, or picks a
//  different dated backup on restore - the same location flow as Games / BIOS / ES-DE settings). A on the
//  last "Back up / Restore now" row runs the op. Backup: to a local folder -> granular.backup_system, or
//  MEGA -> cloud.push_system. Restore (source = a dated backup or "cloud:<ts>"): a LIVE restore
//  (granular.restore category="system") under rule #5 - system config is read by the control panel/helpers,
//  never rewritten by ES-DE, so no staging/restart is needed. Leaving mid-op is allowed (the daemon op keeps
//  running); a cloud backup reattaches to the Landing Transfers tile.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SYSTEM_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SYSTEM_H

#include "guis/mad/MadPage.h"

#include <memory>
#include <set>
#include <string>
#include <vector>

class MadVirtualList;
class TextComponent;

class GuiMadPageSystem : public MadPage
{
public:
    // mode: "backup" (source="live", destination bar at the top) or "restore" (source bar at the top).
    GuiMadPageSystem(GuiMadPanel* panel, const std::string& mode);
    ~GuiMadPageSystem() override;

    void build() override;
    bool input(InputConfig* config, Input input) override;
    bool onBackPressed() override;
    bool consumesSectionNav() override { return false; } // leaving mid-op is allowed (op runs on the daemon)
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onSaveFocus() override;
    void onRestoreFocus() override;

    bool busy() const { return mRunning; }

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
        bool selected;
    };

    bool groupTicked(const Group& g) const;

    void fetchGroups();
    void ensureWidgets();
    void rebuildGroups();
    void updateExplain();
    std::string headerText() const;
    std::string rowText(const Group& g) const;
    void onListSelect(int listIndex);   // rows 0.. = the groups (A ticks); the LAST row = act
    void toggleAt(int groupIndex);
    void act();                   // A on the top "Back up / Restore now" row

    // the destination (backup) / source (restore) bar at the top (X toggles MEGA, Y changes the folder /
    // picks a different dated backup - the same location flow as Games / BIOS).
    void ensureBar();
    void refreshBar();
    std::string barText() const;
    void resolveDefaultDestination(); // cloud-as-default: probe cloud.status first, then default to MEGA (backup + restore)
    void toggleCloud();
    void changeTarget();
    void resolveDefaultSource();
    void openSourcePicker();
    void applySource(const std::string& id, const std::string& created, int count);
    void browseForSource();

    // backup: fire straight to the bar's destination.
    void beginBackupLocal(const std::string& dest);
    void beginBackupCloud();
    // LIVE restore: preview -> replace-warning -> granular.restore (category="system") under rule-5.
    void startRestore();

    void writeItems(MadJson::Writer& w, bool restore) const; // the ticked files as items[]
    bool anyTicked() const;
    void attachRunStream(const std::string& token, bool restore, bool cloud);
    void clearRunStream();

    std::string mMode;            // "backup" | "restore"
    std::string mSource;          // "live" | a backup folder | "cloud:<ts>"
    bool mBackup;

    // dest/source bar state (same as Games/BIOS).
    bool mCloud {false};          // the destination/source is MEGA
    bool mDestTouched {false};  // user pressed X/Y -> the auto cloud-default promote must not override
    bool mDestResolved {false}; // false -> the bar shows "(checking...)" until cloud.status resolves
    std::string mDest;            // backup: the remembered local destination folder
    std::string mSrcCreated;      // restore: the current source's timestamp
    int mSrcCount {0};            // restore: the current source's file count
    bool mHasSource {false};
    bool mChecking {false};
    int mSrcGen {0};              // bumped when the source changes; a stale in-flight resolve bails
    std::shared_ptr<TextComponent> mBar;

    std::vector<Group> mGroups;

    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    std::shared_ptr<TextComponent> mExplain;

    bool mRunning {false};
    bool mRestorePreviewing {false};
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SYSTEM_H
