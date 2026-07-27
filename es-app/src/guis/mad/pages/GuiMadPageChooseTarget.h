//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageChooseTarget.h
//
//  The FIRST step of every granular backup / restore flow (deck-patches): pick WHERE the backup goes
//  (backup) or comes FROM (restore), before drilling into what to back up / restore. It shows two tiles,
//  Local + Cloud (MEGA). In BACKUP mode picking a tile resolves immediately (Local -> a folder picker;
//  Cloud -> a connection check). In RESTORE mode picking a tile shows that type's backup LIST (date + item
//  count, plus a "Browse for a folder..." row for Local), and picking a backup resolves it. On resolution
//  it calls onResolved(target); the CALLER pushes the matching drill page (Games / BIOS / ES-DE settings)
//  ON TOP, so this chooser stays beneath as the back target. The local source RPCs (granular.sources /
//  granular.sources_under) are universal, differentiated only by the ChooserCfg.category param; the cloud
//  source RPC differs per category (see ChooserCfg.cloudSourcesRpc). Extracted from the RView machine that
//  used to live inside GuiMadPageBackupRestore.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_CHOOSE_TARGET_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_CHOOSE_TARGET_H

#include "guis/mad/MadPage.h"

#include <functional>
#include <memory>
#include <string>
#include <vector>

class MadTileGrid;
class MadVirtualList;

// The destination (backup) or source (restore) a granular flow resolves to. Produced by
// GuiMadPageChooseTarget, consumed by a drill page (Games / BIOS / ES-DE settings) in its ctor.
struct MadTarget
{
    bool cloud {false};  // the destination/source is MEGA
    std::string dest;    // backup: the chosen LOCAL folder (empty when cloud)
    std::string source;  // restore: a local backup folder OR "cloud:<ts>" ("cloud:games"/"cloud:bios")
};

// Per-category wiring for the chooser. The local source RPCs are the same for every category (they take a
// `category` param); only the cloud source RPC and the count-label wording differ.
struct ChooserCfg
{
    std::string category;         // "roms" | "bios" | "esde" - sent to granular.sources / sources_under
    std::string countNoun;        // "game" | "file" - the "date - N games/files" label unit ('s' for plural)
    std::string cloudSourcesRpc;  // "granular.cloud_sources" | "bios.cloud_sources" | "esde.cloud_sources"
};

// The three category configs, in ONE place so the Backup landing + Restore hub can't drift out of sync.
namespace MadChooser
{
    ChooserCfg games(); // ROMs / per-game
    ChooserCfg bios();
    ChooserCfg esde();  // ES-DE settings
}

class GuiMadPageChooseTarget : public MadPage
{
public:
    using OnResolved = std::function<void(const MadTarget&)>;

    GuiMadPageChooseTarget(GuiMadPanel* panel, const std::string& mode, const ChooserCfg& cfg,
                           const OnResolved& onResolved);
    ~GuiMadPageChooseTarget() override;

    void build() override;
    void update(int deltaTime) override;
    bool input(InputConfig* config, Input input) override;
    bool onBackPressed() override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onSaveFocus() override;
    void onRestoreFocus() override;

private:
    // One restore source (a local backup folder, or a cloud "cloud:<ts>" set): its id + when it was made
    // + its item count, for the "date - N games/files" row.
    struct Src
    {
        std::string id;
        std::string created;
        int count;
    };
    enum class View { Types, List };

    // Local / Cloud source-type TILES (both modes start here).
    void showTypeTiles();
    void onPickType(const std::string& kind);
    void hideTypeTiles();

    // BACKUP mode: a picked type resolves at once (deferred to update() to stay safe).
    void beginBackupLocal(); // a folder picker -> resolve {dest}
    void beginBackupCloud(); // a cloud.status guard -> resolve {cloud}

    // RESTORE mode: the chosen type's backup LIST, then a picked backup resolves.
    void showBackupList();
    void ensureSourceList();
    void rebuildSourceList();
    void hideSourceList();
    void fetchLocalSources();                              // granular.sources (fast) -> local backups
    void fetchCloudSources();                              // cloudSourcesRpc (slow, async) -> cloud backups
    void openSourceBrowser();                              // "Browse for a folder..." -> GuiMadFolderPicker
    void fetchLocalSourcesUnder(const std::string& path); // granular.sources_under -> merge mBrowsedSrc
    void onPickSource(int index);
    std::string fmtSourceLabel(const std::string& created, int count) const;

    // Fire onResolved(t) on the next update() (never mid-input / mid-callback).
    void resolveWith(const MadTarget& t);

    std::string mMode;   // "backup" | "restore"
    ChooserCfg mCfg;
    OnResolved mOnResolved;

    View mView {View::Types};
    std::shared_ptr<MadTileGrid> mTypeGrid;    // the Local / Cloud tiles (reuses the Backup page icons)
    int mTypeCookie {0};
    std::string mSourceKind;                   // "local" | "cloud" - which backups the List shows

    std::shared_ptr<MadVirtualList> mSourceList;
    std::vector<Src> mLocalSrc;
    std::vector<Src> mCloudSrc;
    std::vector<Src> mBrowsedSrc;              // backups found via the folder browser; kept for the lifetime
    std::vector<std::string> mSourceRowId;     // parallel to the list rows; "" for a note / action row
    bool mLocalLoaded {false};
    bool mCloudLoaded {false};
    bool mCloudConnected {false};
    bool mLocalLoading {false};
    bool mCloudLoading {false};
    int mSourceCookie {0};

    bool mCheckingCloud {false};  // a backup cloud.status check is in flight (guards a double pick)
    bool mHasPending {false};
    MadTarget mPending;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_CHOOSE_TARGET_H
