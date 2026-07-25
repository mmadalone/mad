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

#include <string>
#include <vector>

class GuiMadPageBackupRestore;

class GuiMadPageAssetList : public MadPage
{
public:
    GuiMadPageAssetList(GuiMadPanel* panel, GuiMadPageBackupRestore* root, const std::string& source,
                        const std::string& system, const std::string& stem, const std::string& name,
                        const std::string& art);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    // Block the shoulder section-switch while the root's backup runs (else it destroys the root subpage
    // that owns the op). B is blocked on the root; this covers LB/RB while this leaf is on top.
    bool consumesSectionNav() override;
    void pageScroll(int direction) override;
    void onSaveFocus() override;
    void onRestoreFocus() override;
    std::vector<HelpPrompt> getHelpPrompts() override;

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
    std::string rowGlyph(const Asset& a) const;
    unsigned int rowColor(const Asset& a) const;
    std::string rowText(const Asset& a) const;
    std::string headerText() const;

    void ensureWidgets();
    void populate();
    void toggleAt(int i);
    void act();  // X: back up the ticked asset groups

    GuiMadPageBackupRestore* mRoot;
    std::string mSource;  // "live" (only source that backs up)
    std::string mSystem;
    std::string mStem;
    std::string mName;
    std::string mArt;

    std::vector<Asset> mAssets;
    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    std::shared_ptr<ImageComponent> mPreview;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ASSET_LIST_H
