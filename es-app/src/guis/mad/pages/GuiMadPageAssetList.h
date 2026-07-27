//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageAssetList.h
//
//  Granular Backup & Restore: the GAME-FIRST per-game asset list (deck-patches). For ONE game it shows a
//  short tickable list of the asset groups it has - ROM / Save / Save state / Media (+ later textures /
//  cheats) - each with its size, and the game's box art previewed on the right. A toggles a group, X backs
//  up the ticked groups (via the durable root's granular.backup_assets). Backend: granular.game_assets
//  (the tickable groups) then granular.backup_assets. A transient leaf: the op lives on the root
//  (GuiMadPageBackupRestore), so popping this page never orphans a running backup.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ASSET_LIST_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ASSET_LIST_H

#include "components/ImageComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadVirtualList.h"

#include <set>
#include <string>
#include <vector>

class GuiMadPageBackupRestore;

class GuiMadPageAssetList : public MadPage
{
public:
    // restore=false: source is "live", X BACKS UP the ticked groups (granular.backup_assets via a
    // destination chooser). restore=true: source is a chosen backup, the groups are what the backup HOLDS,
    // and X RESTORES the ticked groups over the live library (granular.restore_assets, rule-5 warned).
    GuiMadPageAssetList(GuiMadPanel* panel, GuiMadPageBackupRestore* root, const std::string& source,
                        const std::string& system, const std::string& stem, const std::string& name,
                        const std::string& art, bool restore = false);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    // Block the shoulder section-switch while the root's backup runs (else it destroys the root subpage
    // that owns the op). B is blocked on the root; this covers LB/RB while this leaf is on top.
    bool consumesSectionNav() override;
    void pageScroll(int direction) override;
    void onSaveFocus() override;
    void onRestoreFocus() override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    // --- media-kind drill (P4): the media-kinds sub-page (GuiMadPageMediaKinds) ticks into THIS page's
    // per-kind set. Called by that drill leaf. source()/system()/stem() feed its granular.game_media fetch.
    std::set<std::string>& mediaKindSel() { return mMediaKinds; }
    // On the drill's first fetch: cache the present kind keys + (first time only) seed the per-kind set from
    // the coarse Media tick (all kinds if Media was ticked, else none).
    void beginMediaDrill(const std::vector<std::string>& presentKeys);
    const std::string& drillSource() const { return mSource; }
    const std::string& drillSystem() const { return mSystem; }
    const std::string& drillStem() const { return mStem; }

private:
    struct Asset {
        std::string key;      // rom / media / saves / states / ...
        std::string label;    // "ROM", "Save", ...
        std::string category; // manifest category
        bool present;
        long long size;
        int count;
        bool selected;
    };
    bool rowSelected(const Asset& a) const;   // media derives from the per-kind set once drilled
    std::string rowGlyph(const Asset& a) const;
    unsigned int rowColor(const Asset& a) const;
    std::string rowText(const Asset& a) const;
    std::string headerText() const;

    void ensureWidgets();
    void populate();
    void toggleAt(int i);
    void act();  // X: back up the ticked asset groups

    int mediaIndex() const;      // index of the "media" asset in mAssets, or -1
    void openMediaDrill();       // Y: pick which media kinds (box art / marquee / ...) to back up / restore
    void refreshMediaRow();      // re-render the Media row + header after the drill changed the selection

    GuiMadPageBackupRestore* mRoot;
    std::string mSource;  // "live" (backup) or a chosen backup folder (restore)
    std::string mSystem;
    std::string mStem;
    std::string mName;
    std::string mArt;
    bool mRestore;        // true: X restores the ticked groups; false: X backs them up

    std::vector<Asset> mAssets;
    // media drill state: once the user opens the drill, the Media row is governed by mMediaKinds (a subset
    // of mMediaKindKeys = the game's present kinds). Until then the coarse Asset.selected governs it.
    bool mMediaDrilled {false};
    std::set<std::string> mMediaKinds;      // ticked media-kind keys (covers / marquees / ...)
    std::vector<std::string> mMediaKindKeys; // the game's PRESENT media kinds (cached from the drill fetch)

    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    std::shared_ptr<ImageComponent> mPreview;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ASSET_LIST_H
