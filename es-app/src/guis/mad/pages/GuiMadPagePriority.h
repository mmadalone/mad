//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePriority.h
//
//  MAD control panel: Priority section (deck-patches) — preferred controller
//  family per system/collection (top = Player 1). Root lists the configured
//  rules, pickers add new ones, and the editor reorders families with
//  carry-mode rows (A lifts, up/down move, A drops, B cancels). Writes via
//  policy.set_ports / policy.clear_ports.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PRIORITY_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PRIORITY_H

#include "components/ButtonComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadChipRow.h"
#include "guis/mad/widgets/MadReorderList.h"
#include "guis/mad/widgets/MadScrollView.h"
#include "guis/mad/widgets/MadTileGrid.h"

#include <string>
#include <vector>

class GuiMadPagePriority : public MadPage
{
public:
    GuiMadPagePriority(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override;

private:
    enum FocusTarget {
        FocusAddSystem = 0,
        FocusSystemGrid = 1,
        FocusAddCollection = 2,
        FocusCollectionGrid = 3
    };

    void rebuild(const rapidjson::Value& result);
    void setFocusTarget(const int target);
    void moveFocus(const int target);
    void followFocus();
    // The next/previous EXISTING focus target from `target` (grids may be
    // absent when nothing is configured).
    int nextTarget(int target, const int direction) const;

    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<TextComponent> mSystemsHeader;
    std::shared_ptr<ButtonComponent> mAddSystem;
    std::shared_ptr<TextComponent> mNoSystems;
    std::shared_ptr<MadTileGrid> mSystemGrid;
    std::shared_ptr<TextComponent> mCollectionsHeader;
    std::shared_ptr<ButtonComponent> mAddCollection;
    std::shared_ptr<TextComponent> mNoCollections;
    std::shared_ptr<MadTileGrid> mCollectionGrid;

    int mFocusTarget;
    int mSystemGridCookie;
    int mCollectionGridCookie;
    float mScrollCookie;
    bool mBuilt;
};

class GuiMadPagePriorityPicker : public MadPage
{
public:
    GuiMadPagePriorityPicker(GuiMadPanel* panel, const std::string& kind);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override;

private:
    // (Re)derives the available list — build and every child pop (an editor
    // SAVE makes its entry unavailable; the Tk picker re-rendered on back).
    void refreshList();

    std::string mKind; // "system" | "collection"
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<MadTileGrid> mGrid;
};

class GuiMadPagePriorityEdit : public MadPage
{
public:
    GuiMadPagePriorityEdit(GuiMadPanel* panel, const std::string& name,
                           const std::string& kind);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    bool onBackPressed() override; // B cancels a reorder carry first.
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    enum FocusTarget {
        FocusLightgun = 0,
        FocusList = 1,
        FocusSave = 2,
        FocusClear = 3
    };

    void rebuild(const rapidjson::Value& result);
    void setFocusTarget(const int target);
    void moveFocus(const int target);
    void followFocus();
    void save();
    void clearRule();

    std::string mName;
    std::string mKind;
    int mNports;
    bool mLightgun; // Saved with Save, like the Tk BooleanVar.

    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mHint;
    std::shared_ptr<MadChipRow> mLightgunChip;
    std::shared_ptr<TextComponent> mLightgunNote;
    std::shared_ptr<MadReorderList> mList;
    std::shared_ptr<ButtonComponent> mSaveButton;
    std::shared_ptr<ButtonComponent> mClearButton;

    int mFocusTarget;
    bool mBuilt;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PRIORITY_H
