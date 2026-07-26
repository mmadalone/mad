//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBios.h
//
//  Granular BIOS backup & restore (deck-patches): the durable ROOT of a BIOS op. Shows the per-system BIOS
//  bucket tiles (bios.systems), drills into ONE bucket's file list (GuiMadPageBiosFiles), and OWNS the
//  running backup/restore so a popped file list never orphans it. Backup mode: source="live", X on the file
//  list opens a destination chooser (ON THIS DECK -> granular.backup_bios / MEGA CLOUD -> cloud.push_bios).
//  Restore mode: source=a local backup folder OR "cloud:<ts>" (a MEGA set), X restores over the live BIOS
//  (granular.restore category="bios", rule-5 warned). source=="cloud" is the sentinel that means "pick a
//  cloud set first": build() then shows the cloud BIOS-backup source list (bios.cloud_sources) before the
//  bucket tiles. Cloud BIOS uses its OWN remote base (bios-backups), so it never crosses the game restore.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H

#include "guis/mad/MadPage.h"

#include <memory>
#include <string>
#include <vector>

class MadTileGrid;
class MadVirtualList;

class GuiMadPageBios : public MadPage
{
public:
    // mode: "backup" (source="live") or "restore" (source = a local backup folder, "cloud:<ts>" for a MEGA
    // set, or the sentinel "cloud" = pick a MEGA set from the cloud source list first).
    GuiMadPageBios(GuiMadPanel* panel, const std::string& mode, const std::string& source);
    ~GuiMadPageBios() override;

    void build() override;
    void update(int deltaTime) override;
    bool input(InputConfig* config, Input input) override;
    bool onBackPressed() override;
    bool consumesSectionNav() override { return mRunning; }
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
    // A cloud source picked from the in-page list is turned into a systems fetch on the next update()
    // (swapping the widget tree from inside the list's own input callback is unsafe).
    enum class Pending { None, ShowSystems };
    struct Bucket {
        std::string key;
        std::string label;
        std::string art;
        int count;
    };
    // One cloud BIOS-backup set: "cloud:<ts>" + when it was made + its file count, for the "date - N" row.
    struct Src {
        std::string id;
        std::string created;
        int count;
    };
    // restore-mode cloud source list (only when the sentinel source "cloud" was passed).
    void showCloudSourceList();
    void ensureSourceList();
    void hideSourceList();
    void rebuildSourceList();
    void fetchCloudSources();
    void onPickSource(int index);
    static std::string fmtSourceLabel(const std::string& created, int count);

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
    std::string mSource;  // "live" | a backup folder | "cloud:<ts>" | "cloud" (sentinel: pick a set first)
    bool mBackup;

    std::vector<Bucket> mBuckets;
    std::shared_ptr<MadTileGrid> mGrid;
    int mGridCookie {0};
    Pending mPending {Pending::None};

    // cloud source list (restore, sentinel "cloud"). mSourceRowId is parallel to the rows: "cloud:<ts>" for
    // a pickable set, "" for a note row.
    bool mPickingSource {false};
    std::shared_ptr<MadVirtualList> mSourceList;
    std::vector<Src> mCloudSrc;
    std::vector<std::string> mSourceRowId;
    bool mCloudLoaded {false};
    bool mCloudLoading {false};
    bool mCloudConnected {false};
    int mSourceCookie {0};

    bool mRunning {false};
    bool mRestorePreviewing {false}; // a restore_preview round-trip is in flight (guards a double X)
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H
