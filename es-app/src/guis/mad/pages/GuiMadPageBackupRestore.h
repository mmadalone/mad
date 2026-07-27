//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBackupRestore.h
//
//  Granular per-game backup OR restore for one mode (deck-patches). Reached AFTER the destination/source
//  is chosen (GuiMadPageChooseTarget), so it opens straight on the per-system tile grid: backup = the live
//  library into a resolved destination (a local folder or MEGA); restore = a resolved backup source (a
//  local folder or "cloud:<ts>"). It is the durable ROOT of the running job, so a backup/restore survives
//  the transient per-game / per-asset subpage being popped. Category = ROMs. "select" mode is the
//  destination-free cross-system game cart used by the whole-config Local/Cloud backup (no target).
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_RESTORE_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_RESTORE_H

#include "guis/mad/MadPage.h"
#include "guis/mad/pages/GuiMadPageChooseTarget.h" // MadTarget (the resolved destination / source)

#include <memory>
#include <set>
#include <string>
#include <vector>

class MadTileGrid;

class GuiMadPageBackupRestore : public MadPage
{
public:
    // mode:
    //   "backup"  - game-first backup of the live library into `target` (a local folder or MEGA).
    //   "restore" - game-first restore FROM `target.source` (a local backup folder or "cloud:<ts>").
    //   "select"  - the live library; A ticks games into `selectionSink`, a cross-system cart owned by the
    //               whole-config Local/Cloud backup page (no target, no X action).
    GuiMadPageBackupRestore(GuiMadPanel* panel, const std::string& mode, const MadTarget& target = {},
                            std::set<std::string>* selectionSink = nullptr);
    ~GuiMadPageBackupRestore() override;

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

    // Durable-root API: the running op lives here so it outlives a popped per-game/asset subpage.
    bool busy() const { return mRunning; }
    // Game-first: the per-system game list (backup mode) drills into ONE game's asset list via this;
    // that leaf backs up the ticked asset groups through startGameAssets (one game, many categories).
    void openGameAssets(const std::string& system, const std::string& stem, const std::string& name,
                        const std::string& art);
    // The leaf's X calls this: back up one game's ticked asset groups to the destination already chosen
    // (mDest / mCloud, resolved by GuiMadPageChooseTarget). Claims mRunning and streams the real backup
    // (local granular.backup_assets{dest} or cloud cloud.push_game_assets).
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
    struct Sys {
        std::string key;
        std::string label;
        std::string art;
        int count;
    };

    void fetchSystems();  // fetch + show the per-system tiles for mSource
    void rebuildSystems();
    void onPickSystem(const std::string& key);
    // The two backup destination branches, each claiming mRunning ONLY when it fires the real backup.
    void beginAssetsLocal(const std::string& system, const std::string& stem,
                          const std::vector<std::string>& keys, const std::string& dest);
    void beginAssetsCloud(const std::string& system, const std::string& stem,
                          const std::vector<std::string>& keys);
    // assets=true: a game-first backup op, whose terminal reports FILES not games. cloud=true: the op
    // streams from cloud.push_game_assets (terminal "Backed up to MEGA." with no local file count).
    void attachRunStream(const std::string& token, bool restore, bool assets = false, bool cloud = false);
    void clearRunStream();

    std::string mMode;      // "backup" | "restore" | "select"
    std::string mCategory;  // "roms" (pilot)
    std::string mSource;    // "live" (backup/select) or a backup folder / "cloud:<ts>" (restore)
    bool mBackup;
    bool mCloud;            // backup: the destination is MEGA (else mDest is a local folder)
    std::string mDest;      // backup: the resolved local destination folder ("" when mCloud)
    std::set<std::string>* mSelectionSink; // non-null = SELECT mode (threaded to the per-game list)

    std::vector<Sys> mSystems;
    std::shared_ptr<MadTileGrid> mGrid;
    int mGridCookie {0};

    bool mRunning {false};
    bool mRestorePreviewing {false}; // a restore_assets_preview round-trip is in flight (guards a double X)
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_RESTORE_H
