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

#include "components/ImageComponent.h"
#include "guis/mad/MadPage.h"

#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

class MadTileGrid;

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
    // While the job runs this page OWNS it; block the shoulder section-switch too (not just B), else a
    // section switch destroys this pushed subpage and orphans the running daemon op (it keeps _GRAN_
    // ACTIVE with no reattach path).
    bool consumesSectionNav() override { return mRunning; }
    void onChildPopped() override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onSaveFocus() override;
    void onRestoreFocus() override;

    // Durable-root API called by the per-game subpage. `items` is (system, stem) for a backup and
    // (system, id) for a restore. The op runs here so it outlives a popped per-game page.
    bool busy() const { return mRunning; }
    void startBackup(const std::string& category,
                     const std::vector<std::pair<std::string, std::string>>& items);
    void startRestore(const std::string& category, const std::string& source,
                      const std::vector<std::pair<std::string, std::string>>& items);

private:
    // A restore source picked from the async chooser is turned into a systems fetch on the next update()
    // (pushing/fetching from the chooser's own onChoose would be undone by its self-pop).
    enum class Pending { None, ShowSystems };
    struct Sys {
        std::string key;
        std::string label;
        std::string art;
        int count;
    };

    void fetchSources();  // restore: choose a backup source, then fetchSystems()
    void fetchSystems();  // fetch + show the per-system tiles for mSource
    void rebuildSystems();
    void onPickSystem(const std::string& key);
    void attachRunStream(const std::string& token, bool restore);
    void clearRunStream();

    std::string mMode;      // "restore" | "select"
    std::string mCategory;  // "roms" (pilot)
    std::string mSource;    // "live" (select) or a backup folder path (restore)
    bool mBackup;
    std::set<std::string>* mSelectionSink; // non-null = SELECT mode (threaded to the per-game list)

    std::vector<Sys> mSystems;
    std::shared_ptr<MadTileGrid> mGrid;
    std::shared_ptr<ImageComponent> mEmblem; // per-game.png emblem (SELECT mode, top-right)
    int mGridCookie {0};
    Pending mPending {Pending::None};

    bool mRunning {false};
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_RESTORE_H
