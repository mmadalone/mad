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
    void openSearch();
    void activateAt(int i);   // A: backup mode -> drill into the game's assets; else toggle selection
    void openAssetsAt(int i); // backup mode: push ONE game's asset list (via the root)
    void toggleAt(int i);
    void act();  // X: restore the selected games (backup mode has no X; A drills)
    // The synthetic LAST row (not in select mode): A backs up / restores ALL of THIS system's games at once
    // (delegated to the durable root's backupAll("system") / restoreAll). true = this system has such a row.
    bool hasAllRow() const { return mSelectionSink == nullptr; }
    void allAction();

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

    std::vector<Game> mGames;
    std::vector<Game> mShown;
    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    std::shared_ptr<ImageComponent> mPreview;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_GRANULAR_GAMES_H
