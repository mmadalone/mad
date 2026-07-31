//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBackupRestore.h
//
//  Granular per-game backup OR restore for one mode (deck-patches). Opens straight on the per-system tile
//  grid with a DESTINATION / SOURCE bar across the top: backup = the live library saved to a chosen place
//  (a remembered local folder, or MEGA); restore = a chosen backup (the latest local set by default, a
//  different one or MEGA via the bar). X on the bar toggles On-this-Deck <-> MEGA; Y changes the folder
//  (backup) or picks a different backup (restore). It is the durable ROOT of the running job, so a
//  backup/restore survives the transient per-game / per-asset subpage being popped. Category = ROMs.
//  "select" mode is the destination-free cross-system game cart used by the whole-config Local/Cloud backup
//  (no bar).
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_RESTORE_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_RESTORE_H

#include "guis/mad/MadPage.h"

#include <memory>
#include <set>
#include <string>
#include <vector>

class MadTileGrid;
class TextComponent;

class GuiMadPageBackupRestore : public MadPage
{
public:
    // mode:
    //   "backup"  - game-first backup of the live library; the destination bar (folder / MEGA) is at the top.
    //   "restore" - game-first restore; the source bar (which backup / MEGA) is at the top.
    //   "select"  - the live library; A ticks games into `selectionSink`, a cross-system cart owned by the
    //               whole-config Local/Cloud backup page (no bar, no X action).
    GuiMadPageBackupRestore(GuiMadPanel* panel, const std::string& mode,
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
    // The leaf's X calls this: back up one game's ticked asset groups to the destination on the bar
    // (mDest / mCloud). Claims mRunning and streams the real backup (local granular.backup_assets{dest} or
    // cloud cloud.push_game_assets). totalBytes = the leaf's Selected total (what the ticked groups
    // weigh); a big MEGA upload confirms first. sizeApprox: the backend hit its sizing budget, so the
    // total is a floor - a cloud run then always confirms, phrased "at least".
    void startGameAssets(const std::string& system, const std::string& stem,
                         const std::vector<std::string>& keys, long long totalBytes,
                         bool sizeApprox);
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
    // The same path for MANY games: the per-system game list's X sends every ticked game with its own
    // ticked asset keys. AssetRestoreSel is just {system, stem, keys} - the shape both backup RPCs take.
    void startGamesAssets(const std::vector<AssetRestoreSel>& games, long long totalBytes,
                          bool sizeApprox);

    // Whole-system / all-systems "All". backupAll: back up EVERY game's ROM + saves + states + media of one
    // system (scope "system", system set) or every system (scope "all", system empty) to the bar's
    // destination, as a DATED snapshot set. restoreAll: restore every game a backup holds (system empty = all
    // systems, else just that one). Both CONFIRM first (a bulk op) and run on the durable root. Called by the
    // systems grid's leading "All" tile (all-systems) and the per-system game list's "All" action row.
    void backupAll(const std::string& scope, const std::string& system);
    void restoreAll(const std::string& system);

private:
    struct Sys {
        std::string key;
        std::string label;
        std::string art;
        int count;
    };

    // The destination (backup) / source (restore) bar at the top of the systems page.
    bool hasBar() const { return mMode != "select"; }
    void ensureBar();
    void refreshBar();
    std::string barText() const;
    void resolveDefaultDestination(); // cloud-as-default: probe cloud.status first, then default to MEGA (backup + restore)
    void toggleCloud();     // X: On this Deck <-> MEGA
    void changeTarget();    // Y: backup = change folder; restore = pick a different backup
    // restore: resolve the latest backup of the CURRENT kind (Local / MEGA) into mSource, then fetch its
    // systems. Called on build() and whenever the kind is toggled.
    void resolveDefaultSource();
    void openSourcePicker();  // restore Y: a flat list of the current kind's backups (+ Browse) to pick from
    void applySource(const std::string& id, const std::string& created, int count); // set + re-fetch systems
    void browseForSource();   // restore Y "Browse for a folder..." -> a folder picker -> newest set found

    void fetchSystems();  // fetch + show the per-system tiles for mSource
    void rebuildSystems();
    void onPickSystem(const std::string& key);
    // The two backup destination branches, each claiming mRunning ONLY when it fires the real backup.
    void beginAssetsLocal(const std::vector<AssetRestoreSel>& games, const std::string& dest);
    void beginAssetsCloud(const std::vector<AssetRestoreSel>& games);
    // The two "All" backup destination branches (local granular.backup_all / cloud cloud.push_game_assets_all),
    // each claiming mRunning ONLY when it fires the real backup.
    void beginBackupAllLocal(const std::string& scope, const std::string& system, const std::string& dest);
    void beginBackupAllCloud(const std::string& scope, const std::string& system);
    // assets=true: a game-first backup op, whose terminal reports FILES not games. cloud=true: the op
    // streams from cloud.push_game_assets (terminal "Backed up to MEGA." with no local file count).
    void attachRunStream(const std::string& token, bool restore, bool assets = false, bool cloud = false);
    void clearRunStream();

    std::string mMode;      // "backup" | "restore" | "select"
    std::string mCategory;  // "roms" (pilot)
    std::string mSource;    // "live" (backup/select) or a backup folder / "cloud:<ts>" (restore)
    bool mBackup;
    std::set<std::string>* mSelectionSink; // non-null = SELECT mode (threaded to the per-game list)

    // dest/source bar state (backup + restore modes).
    bool mCloud {false};     // On this Deck (false) / MEGA (true)
    bool mDestTouched {false};  // user pressed X/Y -> the auto cloud-default promote must not override
    bool mDestResolved {false}; // false -> the bar shows "(checking...)" until cloud.status resolves
    std::string mDest;       // backup: the remembered local destination folder
    std::string mSrcCreated; // restore: the current source's timestamp (for the bar label)
    int mSrcCount {0};       // restore: the current source's game count
    bool mHasSource {false}; // restore: a source is resolved (else "no backup found")
    bool mChecking {false};  // a cloud.status toggle is in flight (guards a double toggle)
    // Bumped whenever the source changes (a toggle-resolve, a Y pick, a Browse). An async source resolve or
    // a systems fetch captures the value at issue time and bails if it no longer matches - so a stale reply
    // (e.g. a resolve from before a fast re-toggle) can never overwrite the current source / systems.
    int mSrcGen {0};
    std::shared_ptr<TextComponent> mBar;

    std::vector<Sys> mSystems;
    std::shared_ptr<MadTileGrid> mGrid;
    int mGridCookie {0};

    bool mRunning {false};
    bool mRestorePreviewing {false}; // a restore_assets_preview round-trip is in flight (guards a double X)
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_RESTORE_H
