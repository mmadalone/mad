//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBackup.h
//
//  MAD control panel: Backup / Restore (deck-patches). Full-system backup via
//  deck-backup.sh (11 include toggles with streamed per-category sizes + a
//  live tally; output lines stream into the footer) and the router-config
//  snapshot/restore quartet from lib/mad_backup. The destructive actions go
//  through a GuiMsgBox confirm.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase.

#include <map>
#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

struct CloudProgress; // guis/mad/pages/GuiMadPageCloudProgress.h
class MadTileGrid;    // guis/mad/widgets/MadTileGrid.h

class GuiMadPageBackup : public MadLightgunPageBase
{
public:
    GuiMadPageBackup(GuiMadPanel* panel);
    ~GuiMadPageBackup();

    void build() override;
    void onChildPopped() override; // returning to the Landing refreshes the Ongoing-transfers tile
    // The Landing section renders a tile grid instead of the base form column, so it routes
    // input/scroll/help/focus to the grid; the Local/Cloud subpages fall through to the base.
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onSaveFocus() override;
    void onRestoreFocus() override;

private:
    // Backup is a tiled landing whose tiles open Local + Cloud SUBPAGES of this same class. The
    // durable cloud-transfer lifecycle (mRunning / mRunToken / mCloudProgress + its stream) lives
    // on the Landing instance (mRoot), which outlives the transient Local/Cloud subpages — so a
    // subpage dtor can never detach a live job.
    // Cloud is now the "Full backup" page (local whole-config archive + the cloud half in one column).
    enum class Section { Landing, Local, Cloud };
    GuiMadPageBackup(GuiMadPanel* panel, GuiMadPageBackup* root, Section section);

    void rebuild(); // Pure local state — safe to re-run on size pushes.
    void rebuildLanding();     // Section::Landing — the 3-tile grid.
    void buildLocalSections(); // the local whole-config archive (part of the "Full backup" page).
    std::string chipLabel(const std::string& key) const;
    void updateTally();
    void onSizePush(const rapidjson::Value& data);
    void runFull(const std::map<std::string, bool>& include); // runs on mRoot (durable stream)
    void openGamesPicker();  // push the SELECT picker into mRoot->mGameSelection (per-game ROMs)
    void runGamesBackup(const std::string& dest); // chained after the config stream (mRoot)
    std::string gamesCountLabel() const;          // "N games chosen" / "no games chosen"
    // The durable per-game cart, read the SAME way by the Local backup (granular.backup) and the Cloud
    // upload (cloud.push_games): "system:stem" ids -> (system, stem) pairs / an "items" params writer.
    std::vector<std::pair<std::string, std::string>> itemsFromSelection() const;
    void fetchDest();        // backup.get_dest -> mRoot->mBackupDest (async; refreshes mDestLabel)
    void openDestPicker();   // GuiMadFolderPicker -> set + persist mRoot->mBackupDest
    std::string destDisplay() const; // mRoot->mBackupDest, or a "loading" placeholder
    void fetchFormat();      // backup.get_format -> mRoot->mFormat (async; rebuilds if it differs)
    void pickFormat();       // A-pressable list of config-archive formats (gzip / store / mirror)
    void setFormat(const std::string& fmt); // persist backup.set_format + refresh the label
    std::string formatDisplay() const;      // human label for mRoot->mFormat

    // Cloud (MEGA) section: state is fetched async, so the section renders from
    // members and re-lays-out (deferRelayout -> rebuild) as cloud.status /
    // cloud.servers land, mirroring how the per-category sizes stream in.
    void fetchCloud();        // issue cloud.status + cloud.servers + categories + sizes
    void fetchCloudStatus();  // cloud.status only (cheap) - refresh connection / last-backup line
    void buildCloudSection(); // render from the fetched state (called by rebuild)
    void pickServer();        // open the A-pressable list of MEGA S4 servers
    void openRestorePicker(); // cloud.snapshots -> pick "latest" or a dated rollback point
    void confirmRestore(const std::string& snapshot); // confirm + restore the chosen version to live
    void setServer(const std::string& id);
    void setCategory(const std::string& key, const bool on);
    // All of the following operate on mRoot's members (the durable Landing instance): a Cloud
    // subpage calls mRoot->startCloudOp(...), passing itself as the progressHost so the progress
    // subpage opens onto the subpage the user is looking at, not the (hidden) Landing.
    void startCloudOp(const std::string& method, const std::string& title,
                      const MadJson::ParamsWriter& params, const std::string& okMsg,
                      MadPage* progressHost, const std::weak_ptr<int>& hostAlive,
                      bool offerRestart = false);
    // offerRestart: on a clean finish, prompt to restart ES-DE (used by the precious restore, whose
    // ES-DE + launchers config is staged and applied by the launch wrapper on the next start).
    void installRunStream(const std::string& token, const std::string& okMsg,
                          bool offerRestart = false); // stream -> mCloudProgress
    void fillProgress(const rapidjson::Value& prog); // a {progress} event -> *mCloudProgress
    // cloud.active -> reattach a running/auto-resumed transfer (mRoot). offerResume=true (build/first
    // entry) also offers the "resume the interrupted restore?" modal; the Landing refocus path passes
    // false so it only ADOPTS a running op (e.g. a granular cloud backup the user backed out of) without
    // re-prompting on every return.
    void fetchActive(bool offerResume = true);
    void promptResumeRestore();  // "resume the interrupted restore?" modal (mRoot)
    void openRestoreLibrary();   // category picker -> restore a library to live
    std::string cloudCatLabel(const std::string& key, const std::string& label) const;
    void updateCloudTally(); // refresh the per-tier "Selected: X" size totals
    bool cloudGuard(); // busy OR not-connected guard for the S4 actions

    bool busyGuard(); // True (with a footer note) while a full backup streams.
    void confirmThen(const std::string& text, const std::function<void()>& action);
    MadBackend::ResponseCallback resultFlash();
    static std::string human(const long long bytes);

    // Section role. Landing (== mRoot) owns the durable transfer state below; Local/Cloud are
    // transient subpages that read display state from the daemon and, for transfers, delegate to
    // mRoot. mRoot points at the Landing instance (itself, for the Landing).
    Section mSection {Section::Landing};
    GuiMadPageBackup* mRoot {this};

    // Landing tile grid (Local / Cloud (MEGA) / Ongoing transfers).
    std::shared_ptr<MadTileGrid> mGrid;
    int mGridCookie {0};

    std::map<std::string, bool> mInclude;  // Full-backup include toggles (durable: lives on mRoot).
    std::string mBackupDest;               // Local-backup destination (durable: lives on mRoot).
    std::shared_ptr<TextComponent> mDestLabel; // "Saving to: <path>" caption (Local subpage).
    std::string mFormat {"gzip"};          // Full-backup config archive format: gzip|store|mirror (durable).
    bool mFormatLoaded {false};            // has backup.get_format landed on mRoot yet?
    std::shared_ptr<TextComponent> mFormatLabel; // "Format: <…>" caption (Local subpage).
    std::map<std::string, long long> mSizes;
    bool mSizesDone;
    bool mRunning; // A full backup OR a cloud transfer is streaming (mRoot's copy is authoritative).
    std::string mSizesToken;
    std::string mRunToken;
    std::shared_ptr<TextComponent> mTally;
    std::vector<std::shared_ptr<MadChipRow>> mChipRows;
    std::set<std::string> mGameSelection; // per-game ROMs chosen for the backup (durable: lives on mRoot)
    std::shared_ptr<TextComponent> mGamesLabel; // "ROMs: N games chosen" caption (Full backup page)
    std::shared_ptr<TextComponent> mCloudGamesLabel; // the same caption on the Cloud section

    // Full backup page: the destination toggle. X swaps On-this-Deck <-> MEGA and the page shows ONLY that
    // destination's controls (buildLocalSections OR buildCloudSection), never both. Cloud-default: mFullCloud
    // is set from a cloud.status probe (MEGA when connected) unless the user pressed X first (mFullTouched).
    bool mFullCloud {false};
    bool mFullResolved {false}; // the cloud.status probe has returned (until then the bar shows "checking...")
    bool mFullTouched {false};  // the user pressed X -> the auto cloud-default must not override

    // Cloud (MEGA) state (fetched async; the section renders once these arrive). The
    // when-to-back-up toggles moved to ES-DE > Other settings (they are global) - no
    // toggle members here anymore; the state files are their single source of truth.
    bool mCloudStatusLoaded {false};
    bool mCloudServersLoaded {false};
    bool mCloudConnected {false};
    std::string mCloudServerId;
    std::string mCloudServerLabel;
    std::string mCloudLastBackup;
    std::vector<std::pair<std::string, std::string>> mCloudServers; // (id, label)

    // Live registered transfers (transfers.list), fetched with fetchActive: the Landing's
    // Transfers tile shows when ANY job is live - a panel-started op, the game-end hook
    // push, a CLI run, or a detached transfer surviving a panel restart (durable: mRoot).
    int mLiveTransfers {0};

    // Own-toggle categories (what the cloud backs up), from cloud.categories.
    bool mCloudCatsLoaded {false};
    std::vector<std::pair<std::string, std::string>> mCatA; // (key,label) Tier A
    std::vector<std::pair<std::string, std::string>> mCatB; // (key,label) Tier B
    std::map<std::string, bool> mCatOn;                     // key -> enabled
    std::shared_ptr<MadChipRow> mCatRowA;
    std::shared_ptr<MadChipRow> mCatRowB;
    std::shared_ptr<TextComponent> mCloudTallyA; // "Selected: X" (Tier A: mCloudSizes)
    std::shared_ptr<TextComponent> mCloudTallyB; // (Tier B: mSizes - it syncs wholesale)

    // Tier-A POST-FILTER upload sizes from cloud.sizes: what the cloud actually sends (after
    // the excludes + skip items), which is smaller than the local full-backup mSizes. Fetched
    // async like backup.sizes; the chips + Tier-A tally prefer these over mSizes.
    std::map<std::string, long long> mCloudSizes;
    bool mCloudSizesDone {false};

    // Live progress, shared with the transfer-progress subpage.
    std::shared_ptr<CloudProgress> mCloudProgress;
    std::string mCloudOpTitle; // title of the running op, to re-open its progress subpage
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_H
