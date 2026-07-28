//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBios.h
//
//  Granular BIOS backup & restore (deck-patches): the durable ROOT of a BIOS op. Opens straight on the
//  per-system BIOS bucket tiles with a DESTINATION / SOURCE bar across the top (X toggles On-this-Deck <->
//  MEGA; Y changes the folder on backup, or picks a different backup on restore). Drills into ONE bucket's
//  file list (GuiMadPageBiosFiles) and OWNS the running backup/restore so a popped file list never orphans
//  it. Backup: X on the file list backs up to the bar's destination (a local folder -> granular.backup_bios,
//  or MEGA -> cloud.push_bios). Restore: X restores over the live BIOS from the bar's source (a local backup
//  folder or "cloud:<ts>"), rule-5 warned. Cloud BIOS uses its OWN remote base (bios-backups).
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H

#include "guis/mad/MadPage.h"

#include <memory>
#include <string>
#include <vector>

class MadTileGrid;
class TextComponent;

class GuiMadPageBios : public MadPage
{
public:
    // mode: "backup" (source="live", destination bar at the top) or "restore" (source bar at the top).
    GuiMadPageBios(GuiMadPanel* panel, const std::string& mode);
    ~GuiMadPageBios() override;

    void build() override;
    bool input(InputConfig* config, Input input) override;
    bool onBackPressed() override;
    // Leaving during a job is allowed (see onBackPressed): the daemon op keeps running, so a shoulder
    // section-switch no longer needs to be blocked (a cloud transfer is adopted by the Landing's Transfers
    // tile; a local copy finishes on its own, rule-5 safe).
    bool consumesSectionNav() override { return false; }
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onSaveFocus() override;
    void onRestoreFocus() override;

    bool busy() const { return mRunning; }
    // Called by the file-list leaf: back up / restore the ticked files of one bucket. The op runs HERE so
    // it outlives a popped leaf. rels are "bios/<path>" keys.
    void startBiosBackup(const std::string& bucket, const std::vector<std::string>& rels);
    void startBiosRestore(const std::string& bucket, const std::vector<std::string>& rels);

private:
    // "All" (the leading grid tile): back up / restore EVERY bucket at once. Backup shows a size-aware confirm
    // (granular.backup_all_size) then fires granular.backup_bios_all / cloud.push_bios_all; restore previews
    // (granular.restore_all_preview) -> replace-warning -> granular.restore_all, category "bios".
    void backupAllBios();
    void restoreAllBios();

    struct Bucket {
        std::string key;
        std::string label;
        std::string art;
        int count;
    };

    // The destination (backup) / source (restore) bar at the top of the bucket page.
    void ensureBar();
    void refreshBar();
    std::string barText() const;
    void resolveDefaultDestination(); // cloud-as-default: probe cloud.status first, then default to MEGA (backup + restore)
    void toggleCloud();     // X: On this Deck <-> MEGA
    void changeTarget();    // Y: backup = change folder; restore = pick a different backup
    void resolveDefaultSource();
    void openSourcePicker();
    void applySource(const std::string& id, const std::string& created, int count);
    void browseForSource();

    void fetchSystems();
    void rebuildSystems();
    void onPickBucket(const std::string& key);
    // the two backup destination branches (each claims mRunning only when its real backup fires).
    void beginBiosBackupLocal(const std::string& bucket, const std::vector<std::string>& rels,
                              const std::string& dest);
    void beginBiosBackupCloud(const std::string& bucket, const std::vector<std::string>& rels);
    // cloud=true: the op streams from cloud.push_bios (terminal "Backed up BIOS to MEGA." - no file count).
    void attachRunStream(const std::string& token, bool restore, bool cloud = false);
    void clearRunStream();

    std::string mMode;    // "backup" | "restore"
    std::string mSource;  // "live" (backup) | a backup folder | "cloud:<ts>" (restore)
    bool mBackup;

    // dest/source bar state.
    bool mCloud {false};
    bool mDestTouched {false};  // user pressed X/Y -> the auto cloud-default promote must not override
    bool mDestResolved {false}; // false -> the bar shows "(checking...)" until cloud.status resolves
    std::string mDest;       // backup: the remembered local destination folder
    std::string mSrcCreated; // restore: the current source's timestamp
    int mSrcCount {0};       // restore: the current source's file count
    bool mHasSource {false};
    bool mChecking {false};
    // Bumped whenever the source changes; an in-flight resolve / systems fetch bails if superseded (see
    // GuiMadPageBackupRestore for the rationale) so a stale reply can't overwrite the current source.
    int mSrcGen {0};
    std::shared_ptr<TextComponent> mBar;

    std::vector<Bucket> mBuckets;
    std::shared_ptr<MadTileGrid> mGrid;
    int mGridCookie {0};

    bool mRunning {false};
    bool mRestorePreviewing {false}; // a restore_preview round-trip is in flight (guards a double X)
    bool mPreflight {false};         // an "All" size-fetch is in flight (guards a double All-tile confirm)
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H
