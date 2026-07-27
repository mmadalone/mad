//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBackupRestore.h
//
//  Granular per-game backup OR restore for one mode (deck-patches), reached from the Backup page's
//  "Backup" / "Restore" tiles. It shows the per-system tile grid (backup = the live library; restore =
//  a chosen local backup) and is the durable ROOT of the running job, so a backup/restore survives the
//  transient per-game subpage being popped. Pilot category = ROMs. Backend: granular.sources /
//  granular.browse (systems) then granular.backup / granular.restore.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_RESTORE_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_RESTORE_H

#include "guis/mad/MadPage.h"

#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

class MadTileGrid;
class MadVirtualList;

class GuiMadPageBackupRestore : public MadPage
{
public:
    // mode: "restore" (source is a chosen local backup; X restores) or "select" (source is the live
    // library; A ticks games into `selectionSink`, a cross-system cart owned by the Local/Cloud backup
    // page). Restore passes selectionSink=nullptr.
    GuiMadPageBackupRestore(GuiMadPanel* panel, const std::string& mode,
                            std::set<std::string>* selectionSink = nullptr);
    ~GuiMadPageBackupRestore() override;

    void build() override;
    void update(int deltaTime) override;
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

    // Durable-root API: the running op lives here so it outlives a popped per-game/asset subpage.
    bool busy() const { return mRunning; }
    // Game-first: the per-system game list (backup mode) drills into ONE game's asset list via this;
    // that leaf backs up the ticked asset groups through startGameAssets (one game, many categories).
    void openGameAssets(const std::string& system, const std::string& stem, const std::string& name,
                        const std::string& art);
    // The leaf's X calls this: it opens a destination chooser (ON THIS DECK / MEGA CLOUD) rather than
    // backing up immediately, so the user picks where a game's assets go. Each branch then claims mRunning
    // and streams the real backup (local granular.backup_assets{dest} or cloud cloud.push_game_assets).
    void startGameAssets(const std::string& system, const std::string& stem,
                         const std::vector<std::string>& keys);
    // Game-first RESTORE: restore one or more games' ticked asset groups over the live library. The asset
    // leaf (one game, its ticked groups) AND the per-system game list (bulk; keys empty = ALL of each
    // ticked game's backed-up assets) both call this. It previews (granular.restore_assets_preview) to WARN
    // before overwriting, then streams granular.restore_assets under rule #5. Runs on the durable root so a
    // popped leaf/list never orphans it.
    struct AssetRestoreSel {
        std::string system;
        std::string stem;
        std::vector<std::string> keys;  // empty = restore every asset the backup holds for this game
    };
    void restoreAssets(const std::vector<AssetRestoreSel>& games);

private:
    // A restore source picked from the in-page source list is turned into a systems fetch on the next
    // update() (swapping the widget tree from inside the list's own input callback is unsafe).
    enum class Pending { None, ShowSystems };
    struct Sys {
        std::string key;
        std::string label;
        std::string art;
        int count;
    };
    // One restore source (a local backup folder, or a cloud "cloud:<ts>" set): its id + when it was made
    // + its game count, for the "date - N games" row.
    struct Src {
        std::string id;
        std::string created;
        int count;
    };

    // restore drill-down: Local/Cloud source-type TILES -> that type's backup LIST -> its per-system tiles.
    void showTypeTiles();       // the restore landing (2 tiles) + the back target from the backup list
    void onPickType(const std::string& kind);
    void hideTypeTiles();
    void showBackupList();      // the chosen type's backups (back target from the systems tiles)
    void ensureSourceList();
    void rebuildSourceList();   // rows from mLocalSrc / mCloudSrc for the chosen kind (+ loading/empty notes)
    void hideSourceList();
    void hideSystems();
    void fetchLocalSources();   // granular.sources (fast) -> local backups
    void fetchCloudSources();   // granular.cloud_sources (slow, async) -> cloud backups
    void openSourceBrowser();   // the "Browse for a folder..." row -> GuiMadFolderPicker (local only)
    void fetchLocalSourcesUnder(const std::string& path); // granular.sources_under -> merge mBrowsedSrc
    void onPickSource(int index);
    static std::string fmtSourceLabel(const std::string& created, int count);

    void fetchSystems();  // fetch + show the per-system tiles for mSource
    void rebuildSystems();
    void onPickSystem(const std::string& key);
    // The two destination branches of startGameAssets, each claiming mRunning ONLY when it fires the real
    // backup (a cancelled chooser/picker pins nothing). Local writes to a picked folder; cloud to MEGA.
    void beginAssetsLocal(const std::string& system, const std::string& stem,
                          const std::vector<std::string>& keys, const std::string& dest);
    void beginAssetsCloud(const std::string& system, const std::string& stem,
                          const std::vector<std::string>& keys);
    // assets=true: a game-first backup op, whose terminal reports FILES not games. cloud=true: the op
    // streams from cloud.push_game_assets (terminal "Backed up to MEGA." with no local file count).
    void attachRunStream(const std::string& token, bool restore, bool assets = false, bool cloud = false);
    void clearRunStream();

    std::string mMode;      // "restore" | "select"
    std::string mCategory;  // "roms" (pilot)
    std::string mSource;    // "live" (select) or a backup folder path (restore)
    bool mBackup;
    std::set<std::string>* mSelectionSink; // non-null = SELECT mode (threaded to the per-game list)

    std::vector<Sys> mSystems;
    std::shared_ptr<MadTileGrid> mGrid;
    int mGridCookie {0};
    Pending mPending {Pending::None};

    // restore drill-down view. Types = the Local/Cloud source-type tiles; List = the chosen type's backups;
    // Systems = the per-system tiles. select mode is always Systems. mSourceRowId is parallel to the list
    // rows: the source id for a pickable backup row, "" for a note row.
    enum class RView { Types, List, Systems };
    RView mRView {RView::Systems};
    std::shared_ptr<MadTileGrid> mTypeGrid;   // the Local / Cloud source-type tiles (reuses the Backup icons)
    std::string mSourceKind;                  // "local" | "cloud" - which backups the List shows
    int mTypeCookie {0};
    std::shared_ptr<MadVirtualList> mSourceList;
    std::vector<Src> mLocalSrc;
    std::vector<Src> mCloudSrc;
    std::vector<Src> mBrowsedSrc;  // backups found via the folder browser; kept for the page's lifetime
    std::vector<std::string> mSourceRowId;
    bool mLocalLoaded {false};
    bool mCloudLoaded {false};
    bool mCloudConnected {false};
    bool mLocalLoading {false}; // an in-flight granular.sources request (don't fire a duplicate)
    bool mCloudLoading {false}; // an in-flight granular.cloud_sources request (the slow one)
    int mSourceCookie {0};

    bool mRunning {false};
    bool mRestorePreviewing {false}; // a restore_assets_preview round-trip is in flight (guards a double X)
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_RESTORE_H
