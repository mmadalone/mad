//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadBackupTilePage.h
//
//  MAD control panel: the tile-grid half of the backup flow (deck-patches).
//  BIOS and emulator config present their category as a grid of tiles - one per
//  BIOS bucket / per emulator, behind a leading "All" tile - and drill into a
//  leaf that ticks what to take. This page is the durable ROOT of the op, so a
//  popped leaf never orphans a running backup.
//
//  The "All" tile is what makes this half bigger than the group list: backing up
//  everything asks the daemon for a real size first and confirms before pulling
//  data, and restoring everything previews before it warns.
//

#ifndef ES_APP_GUIS_MAD_MAD_BACKUP_TILE_PAGE_H
#define ES_APP_GUIS_MAD_MAD_BACKUP_TILE_PAGE_H

#include "guis/mad/MadBackupFlowPage.h"

#include <memory>
#include <string>
#include <vector>

class MadTileGrid;

// One ticked backup item: a file rel plus the display GROUP it belongs to (the backup manifest tags
// each item with its group so a restore can regroup it). BIOS has no groups and leaves it empty.
struct MadBackupItem {
    std::string group;
    std::string rel;
};

class MadBackupTilePage : public MadBackupFlowPage
{
public:
    bool input(InputConfig* config, Input input) override;
    bool onBackPressed() override;
    // Leaving during a job is allowed (see onBackPressed): the daemon op keeps running, so a shoulder
    // section-switch does not need blocking (a cloud transfer is adopted by the Landing's Transfers
    // tile; a local copy finishes on its own, rule-5 safe).
    bool consumesSectionNav() override { return false; }
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onSaveFocus() override;
    void onRestoreFocus() override;

    // Called by the leaf: back up / restore the ticked items of ONE tile. The op runs HERE so it
    // outlives a popped leaf.
    void startBackup(const std::string& key, const std::vector<MadBackupItem>& items);
    void startRestore(const std::string& key, const std::vector<std::string>& rels);

protected:
    struct Item {
        std::string key;
        std::string label;
        std::string art;
    };

    // The tile half's own per-category data, on top of MadBackupFlowPage::Params.
    struct TileParams {
        std::string itemKeyName;    // "bucket" / "emulator" - the items[] key naming the tile
        std::string backupAllRpc;   // "granular.backup_bios_all"
        std::string cloudPushAllRpc;// "cloud.push_bios_all"
        std::string categoryIcon;   // "backup-bios" - the All tile's art and the per-tile fallback
        std::string allConfirmNoun; // "ALL BIOS" / "ALL emulator config"
        std::string allConfirmExtra;// ", includes texture/mod packs" (emulator config only)
        std::string allDurationWord;// "a while" / "a long time"
        std::string emptyBackup;    // "No BIOS files found."
        std::string emptyRestore;   // "This backup has no BIOS."
    };

    MadBackupTilePage(GuiMadPanel* panel, const std::string& title, const std::string& mode,
                      const Params& params, const TileParams& tileParams);

    void build() override;

    // ── hooks ────────────────────────────────────────────────────────────────
    // The tile's art. The default is whatever the backend resolved, else the category icon; BIOS adds
    // a dedicated icon for its "other" bucket.
    virtual std::string tileArt(const Item& i) const;
    // The drill-down for one tile.
    virtual MadPage* makeLeafPage(const std::string& key, const std::string& label) = 0;

    // MadBackupFlowPage
    void refetchForSource() override;
    void teardownContent() override;

    const TileParams mTileParams;
    std::vector<Item> mItems;
    std::shared_ptr<MadTileGrid> mGrid;
    int mGridCookie {0};
    bool mPreflight {false}; // an All-tile size probe / preview is in flight

private:
    void fetchTiles();
    void rebuildTiles();
    void onPickTile(const std::string& key);
    void writeItems(MadJson::Writer& w, const std::string& key,
                    const std::vector<MadBackupItem>& items) const;
    void beginBackupLocal(const std::string& key, const std::vector<MadBackupItem>& items,
                          const std::string& dest);
    void beginBackupCloud(const std::string& key, const std::vector<MadBackupItem>& items);
    void backupAll();
    void restoreAll();
};

#endif // ES_APP_GUIS_MAD_MAD_BACKUP_TILE_PAGE_H
