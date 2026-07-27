//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBios.h
//
//  Granular BIOS backup & restore (deck-patches): the durable ROOT of a BIOS op. Reached AFTER the
//  destination/source is chosen (GuiMadPageChooseTarget), so it opens straight on the per-system BIOS
//  bucket tiles (bios.systems), drills into ONE bucket's file list (GuiMadPageBiosFiles), and OWNS the
//  running backup/restore so a popped file list never orphans it. Backup mode: source="live", X on the file
//  list backs up to the resolved destination (a local folder -> granular.backup_bios, or MEGA ->
//  cloud.push_bios). Restore mode: source = a local backup folder OR "cloud:<ts>" (a MEGA set), X restores
//  over the live BIOS (granular.restore category="bios", rule-5 warned). Cloud BIOS uses its OWN remote base
//  (bios-backups), so it never crosses the game restore.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H

#include "guis/mad/MadPage.h"
#include "guis/mad/pages/GuiMadPageChooseTarget.h" // MadTarget (the resolved destination / source)

#include <memory>
#include <string>
#include <vector>

class MadTileGrid;

class GuiMadPageBios : public MadPage
{
public:
    // mode: "backup" (source="live", backs up to `target`) or "restore" (source = target.source, a local
    // backup folder or "cloud:<ts>").
    GuiMadPageBios(GuiMadPanel* panel, const std::string& mode, const MadTarget& target);
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
    struct Bucket {
        std::string key;
        std::string label;
        std::string art;
        int count;
    };

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
    bool mCloud;          // backup: the destination is MEGA (else mDest is a local folder)
    std::string mDest;    // backup: the resolved local destination folder ("" when mCloud)

    std::vector<Bucket> mBuckets;
    std::shared_ptr<MadTileGrid> mGrid;
    int mGridCookie {0};

    bool mRunning {false};
    bool mRestorePreviewing {false}; // a restore_preview round-trip is in flight (guards a double X)
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H
