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
    enum class Section { Landing, Local, Cloud };
    GuiMadPageBackup(GuiMadPanel* panel, GuiMadPageBackup* root, Section section);

    void rebuild(); // Pure local state — safe to re-run on size pushes.
    void rebuildLanding();     // Section::Landing — the 3-tile grid.
    void buildLocalSections(); // Section::Local — Full backup + Router config backup.
    std::string chipLabel(const std::string& key) const;
    void updateTally();
    void onSizePush(const rapidjson::Value& data);
    void runFull(const std::map<std::string, bool>& include); // runs on mRoot (durable stream)

    // Cloud (MEGA) section: state is fetched async, so the section renders from
    // members and re-lays-out (deferRelayout -> rebuild) as cloud.status /
    // cloud.servers land, mirroring how the per-category sizes stream in.
    void fetchCloud();        // issue cloud.status + cloud.servers + categories + sizes
    void fetchCloudStatus();  // cloud.status only (cheap) - refresh connection / last-backup line
    void buildCloudSection(); // render from the fetched state (called by rebuild)
    void pickServer();        // open the A-pressable list of MEGA S4 servers
    void setServer(const std::string& id);
    void setCloudToggle(const std::string& which, const bool on);
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
    void fetchActive();          // cloud.active -> reattach a running/auto-resumed transfer (mRoot)
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
    std::map<std::string, long long> mSizes;
    bool mSizesDone;
    bool mRunning; // A full backup OR a cloud transfer is streaming (mRoot's copy is authoritative).
    std::string mSizesToken;
    std::string mRunToken;
    std::shared_ptr<TextComponent> mTally;
    std::vector<std::shared_ptr<MadChipRow>> mChipRows;

    // Cloud (MEGA) state (fetched async; the section renders once these arrive).
    bool mCloudStatusLoaded {false};
    bool mCloudServersLoaded {false};
    bool mCloudConnected {false};
    bool mCloudOnExit {false};
    bool mCloudTimer {false};
    bool mCloudAutoResume {false}; // cloud.status autoresume_enabled: re-launch interrupted transfers
    std::string mCloudServerId;
    std::string mCloudServerLabel;
    std::string mCloudLastBackup;
    std::vector<std::pair<std::string, std::string>> mCloudServers; // (id, label)
    std::shared_ptr<MadChipRow> mCloudToggleRow;

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
