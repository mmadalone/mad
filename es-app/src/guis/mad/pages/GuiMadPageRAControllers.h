//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRAControllers.h
//
//  MAD control panel: RetroArch hub -> Controllers section (deck-patches).
//  Root page for the controller-priority policy: renders the GLOBAL scope
//  INLINE (connected families, X-Arcade warn toggles via a MadToggleList, and
//  a MadReorderList editor for the default type-priority order, top =
//  Player 1), and below it the SAME "Configured systems/collections" list the
//  (now-retired) Priority root page showed, pushing the existing
//  GuiMadPagePriorityPicker / GuiMadPagePriorityEdit for per-system/collection
//  scopes (not reimplemented here). Global reads via racontrollers.get, writes
//  via policy.set_scope_flag (toggles) / policy.set_ports / policy.clear_ports
//  (the order); the systems/collections list reuses priority.list verbatim.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RA_CONTROLLERS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RA_CONTROLLERS_H

#include "components/ButtonComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadReorderList.h"
#include "guis/mad/widgets/MadScrollView.h"
#include "guis/mad/widgets/MadTileGrid.h"
#include "guis/mad/widgets/MadToggleList.h"

#include <string>
#include <vector>

class GuiMadPageRAControllers : public MadPage
{
public:
    GuiMadPageRAControllers(GuiMadPanel* panel, const std::string& title);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    bool onBackPressed() override; // B cancels a global reorder carry first.
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override;

private:
    // Top-to-bottom focus order: the inline global block first, then the same
    // "configured systems/collections" targets the Priority root used.
    enum FocusTarget {
        FocusToggles = 0,
        FocusReorderList = 1,
        FocusSave = 2,
        FocusClear = 3,
        FocusAddSystem = 4,
        FocusSystemGrid = 5,
        FocusAddCollection = 6,
        FocusCollectionGrid = 7
    };

    void requestSystemsList(); // priority.list -> rebuild()
    void rebuild(const rapidjson::Value& priorityResult);
    void setFocusTarget(const int target);
    void moveFocus(const int target);
    void followFocus();
    // The next/previous EXISTING focus target from `target` (the toggle block
    // and either grid may be absent).
    int nextTarget(int target, const int direction) const;
    void setScopeFlag(const std::string& flag, bool value);
    void saveGlobalOrder();
    void clearGlobalOrder();

    // Copied out of racontrollers.get's payload: these must outlive that async
    // callback for the second (priority.list) round trip, so they can't stay
    // rapidjson::Value references.
    std::vector<std::string> mGlobalOrder;
    int mNports;
    std::vector<std::string> mConnectedFamilies;
    std::vector<MadToggleList::Item> mToggleItems;

    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<TextComponent> mConnectedLine;
    std::shared_ptr<MadToggleList> mToggleList; // null when this scope has no toggles
    std::shared_ptr<TextComponent> mHint;
    std::shared_ptr<MadReorderList> mGlobalList;
    std::shared_ptr<ButtonComponent> mSaveButton;
    std::shared_ptr<ButtonComponent> mClearButton;
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

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RA_CONTROLLERS_H
