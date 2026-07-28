//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageControllers.h
//
//  Granular CONTROLLER config backup & LIVE restore (deck-patches, P12b Build B): the durable ROOT of a
//  controller-config op. Opens on the tickable GROUPS (controllers.groups) - Emulator controller configs /
//  Routing overrides - with a plain-English explanation side pane and a DESTINATION / SOURCE bar across the
//  top (X toggles On-this-Deck <-> MEGA; Y changes the folder on backup, or picks a different dated backup on
//  restore - the same location flow as Games / BIOS / System). A on the "Back up / Restore now" row runs the
//  op. Backup: to a local folder -> granular.backup_controllers, or MEGA -> cloud.push_controllers. Restore
//  (source = a dated backup or "cloud:<ts>"): a LIVE restore (granular.restore category="controllers") under
//  rule #5, bounded by controllers_map's allowlist. Restore mode also offers two LOCAL revert ops (revert
//  emulator inputs to their pre-MAD .router-backup; reset the routing overrides). Overlaps Emulator config +
//  System by design (a dedicated one-tile controller-config backup). Leaving mid-op is allowed.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_CONTROLLERS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_CONTROLLERS_H

#include "guis/mad/MadPage.h"

#include <memory>
#include <set>
#include <string>
#include <vector>

class MadVirtualList;
class TextComponent;

class GuiMadPageControllers : public MadPage
{
public:
    // mode: "backup" (source="live", destination bar at the top) or "restore" (source bar at the top).
    GuiMadPageControllers(GuiMadPanel* panel, const std::string& mode);
    ~GuiMadPageControllers() override;

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
    void onListSelect(int listIndex);   // rows 0.. groups (A ticks); action row = act; +2 revert rows in restore
    void toggleAt(int groupIndex);
    void act();                   // A on the "Back up / Restore now" row
    // Restore-mode-only LOCAL revert ops (not tied to a backup source), kept from the old page:
    void revertInputBackups();    // backup.restore_router - revert every emulator input to its pre-MAD backup
    void resetOverrides();        // backup.reset_local   - delete controller-policy.local.toml (defaults)

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
    // LIVE restore: preview -> replace-warning -> granular.restore (category="controllers") under rule-5.
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

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_CONTROLLERS_H
