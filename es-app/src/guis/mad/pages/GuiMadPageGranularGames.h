//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageGranularGames.h
//
//  Granular Backup & Restore: the per-game multi-select list for ONE system (deck-patches). A virtualized
//  list of the system's games with ●/○ selection glyphs and the focused game's box art previewed on the
//  right; A toggles selection, Y searches, X backs up / restores the selected games. A game whose ROM is
//  absent on disk (backup browse) shows dimmed with a ⚠ and can't be selected. Mirrors
//  GuiMadPageBezelPerGame. Backend: granular.browse (items), then granular.backup / granular.restore via
//  the durable root (GuiMadPageBackupRestore).
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_GRANULAR_GAMES_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_GRANULAR_GAMES_H

#include "components/ImageComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadVirtualList.h"

#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

class GuiMadPageBackupRestore;

class GuiMadPageGranularGames : public MadPage
{
public:
    // mode: "restore" (X restores the selected games via the root); "select" (A toggles games into
    // `selectionSink` - a cross-system cart owned by the Local/Cloud backup page - with no X action);
    // or "backup" (game-first: A DRILLS into one game's asset tick list via the root, no multi-select).
    GuiMadPageGranularGames(GuiMadPanel* panel, GuiMadPageBackupRestore* root,
                            const std::string& category, const std::string& source,
                            const std::string& mode, const std::string& system,
                            const std::string& systemLabel,
                            std::set<std::string>* selectionSink = nullptr);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    // Block the shoulder section-switch while the root's job runs (else it destroys the root subpage
    // that owns the op). B is blocked on the root page; this covers LB/RB while this page is on top.
    bool consumesSectionNav() override;
    void pageScroll(int direction) override;
    void onSaveFocus() override;
    void onRestoreFocus() override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct Game {
        std::string id;    // "system:stem" — the restore write key
        std::string stem;  // rom stem — the backup write key
        std::string name;  // gamelist <name> for display
        std::string art;   // absolute box-art path ("" -> preview transparent)
        bool present;      // ROM on disk (backup browse); always true for a restore browse
        bool selected;
    };
    std::string rowGlyph(const Game& game) const;
    unsigned int rowColor(const Game& game) const;
    static std::string rowText(const Game& game) { return game.name.empty() ? game.stem : game.name; }
    std::string headerText() const;

    void ensureWidgets();
    void populate();
    void updatePreview();
    // UNDER the box art: the focused game's assets with their sizes, that game's ticked total, and the
    // whole system's ticked total. The panel belongs anywhere the sizes would be TRUE and there is something to tick: a live
    // source (a restore browse's truth is the backup's manifest, not the live library), and either
    // the cross-system cart or the backup browse - which now ticks games too.
    bool sizesApply() const { return mSource == "live"; }
    void refreshSelectionSizes();  // fire granular.selection_sizes for the current selection
    void updateDetail();           // render mSelSizes/mSelTotal into mDetail
    void openSearch();
    void activateAt(int i);   // A: backup mode -> drill into the game's assets; else toggle selection
    void openAssetsAt(int i); // backup mode: push ONE game's asset list (via the root)
    void toggleAt(int i);   // i is a SHOWN-games index (the top all-row is handled before this)
    void act();  // X: BACKUP -> back up ALL games in this system; RESTORE -> restore the ticked games
    // RESTORE gets a synthetic TOP row "> Restore all N games" (A -> restoreAll); BACKUP uses Square (X) for
    // "back up all" with no row (A still drills one game); SELECT mode has neither.
    bool hasTopAllRow() const { return restoreMode(); }
    void allAction();  // back up / restore ALL of this system's games (via the durable root)

    GuiMadPageBackupRestore* mRoot;
    std::string mCategory;
    std::string mSource;  // "live" (backup) or a backup folder path (restore)
    std::string mMode;    // "backup" | "restore"
    std::string mSystem;
    std::string mLabel;
    std::string mFilter;
    bool mBackup;
    // restore mode (local OR cloud): X restores ALL of each ticked game's assets (game-first restore_assets,
    // which handles per-asset AND whole-ROM backups via item_game); Y drills into one game's per-asset picks.
    bool restoreMode() const { return !mBackup && mSelectionSink == nullptr; }
    std::set<std::string>* mSelectionSink; // non-null = SELECT mode (toggle into this cart, no X action)

    struct SelSize {
        std::string name;
        long long size;
        bool partial;  // the backend ran out of sizing budget for this one - a floor, not a fact
    };

    std::vector<Game> mGames;
    std::vector<Game> mShown;
    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    std::shared_ptr<ImageComponent> mPreview;
    std::shared_ptr<TextComponent> mDetail;

    // Per-game asset sizes streamed by granular.system_sizes, keyed by stem. The stream is exact but
    // slow on a big system (measured: 51 s for fba's 1828 games), so it fills in progressively and the
    // panel shows its progress; once here, every tick is arithmetic rather than another disk walk.
    struct AssetSize {
        std::string key;
        std::string label;
        long long size;
        int count;
    };
    std::map<std::string, std::vector<AssetSize>> mGameAssets;   // stem -> its assets
    bool mSizesStreaming {false};
    int mSizedN {0};
    int mSizeTotalN {0};
    std::string mSizeStreamToken;

    long long gameTickedSize(const std::string& stem) const;  // ticked assets of ONE game
    long long tickedTotal() const;                            // every ticked game in this system
    void startSystemSizes();
    void toggleGameAt(int i);      // Y: include/exclude a whole game
    void selectAllOrNone();        // R3: every game on, or every game off
    void backupTicked();           // X: back up exactly what is ticked

    std::vector<SelSize> mSelSizes;
    long long mSelTotal {0};
    bool mSelPartial {false};   // some game could not be fully sized -> the total is a floor
    int mSelSkipped {0};        // games the backend had no budget left to size
    // Sizing walks the disk, so a rapid A-A-A must not stack requests: only ONE is ever in flight,
    // a toggle during it just sets mSelDirty, and the reply re-fires if the selection moved on.
    // mSelGen additionally drops a stale reply that lands after the selection changed.
    bool mSelInFlight {false};
    bool mSelDirty {false};
    unsigned int mSelGen {0};
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_GRANULAR_GAMES_H
