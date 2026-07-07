//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRAControllers.h
//
//  MAD control panel: RetroArch hub -> Controllers section (deck-patches).
//  Short root page: connected families plus a MadReorderList editor for the
//  default GLOBAL type-priority order (top = Player 1) with Save/Clear, and
//  a button pushing GuiMadPagePriority — the subpage listing every present
//  system + collection for their own per-scope rules (and, per system, its
//  X-Arcade warn toggle). Global reads via racontrollers.get{scope:"global"};
//  writes via policy.set_ports / policy.clear_ports (the order). No warn
//  toggles or system/collection lists live inline here anymore.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RA_CONTROLLERS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RA_CONTROLLERS_H

#include "components/ButtonComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadReorderList.h"
#include "guis/mad/widgets/MadScrollView.h"

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
    // Buffered X=Save / Y=Cancel: the reorder list stages the order in the
    // frontend; dirty = the staged order differs from the last-saved baseline.
    bool madSave() override;
    bool madCancel() override;
    bool hasUnsavedEdits() const override;

    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override;

private:
    enum FocusTarget {
        FocusReorderList = 0,
        FocusSave = 1,
        FocusClear = 2,
        FocusSubpage = 3
    };

    void rebuild();
    void setFocusTarget(const int target);
    void moveFocus(const int target);
    void followFocus();
    int nextTarget(int target, const int direction) const;
    void saveGlobalOrder();
    void clearGlobalOrder();
    bool isDirty() const; // staged reorder differs from mGlobalOrder (the baseline)

    std::vector<std::string> mGlobalOrder;
    int mNports;
    std::vector<std::string> mConnectedFamilies;

    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<TextComponent> mConnectedLine;
    std::shared_ptr<TextComponent> mHint;
    std::shared_ptr<MadReorderList> mGlobalList;
    std::shared_ptr<ButtonComponent> mSaveButton;
    std::shared_ptr<ButtonComponent> mClearButton;
    std::shared_ptr<ButtonComponent> mSubpageButton;

    int mFocusTarget;
    float mScrollCookie;
    bool mBuilt;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RA_CONTROLLERS_H
