//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePergamePads.h
//
//  MAD control panel: PER-GAME "Controllers -> pads -> players" reorder page
//  (deck-patches). Opened from a per-game picker for standard PCSX2 (namespace
//  pcsx2pgin). A carry-mode reorder list (top = Player 1); Apply stores a per-game
//  controller-TYPE priority order that the launch router applies as an override to
//  the global pads -> players order for THIS game only (reverted on exit). No
//  hands-off here (that is a global setting on the per-emulator Controllers page).
//  A trimmed sibling of GuiMadPagePadsPriority. Data: <ns>.pads_get /
//  <ns>.pads_set_order (keyed by titleid).
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PERGAME_PADS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PERGAME_PADS_H

#include "components/ButtonComponent.h"
#include "components/TextComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadReorderList.h"
#include "guis/mad/widgets/MadScrollView.h"

#include <map>
#include <string>
#include <vector>

class GuiMadPagePergamePads : public MadPage
{
public:
    GuiMadPagePergamePads(GuiMadPanel* panel, const std::string& title, const std::string& ns,
                          const std::string& titleid);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    bool onBackPressed() override; // B cancels a reorder carry first.
    std::vector<HelpPrompt> getHelpPrompts() override;
    // Buffered X=Save / Y=Cancel: the reorder list stages the order; dirty = the
    // staged order differs from the baseline captured at load.
    bool madSave() override;
    bool madCancel() override;
    bool hasUnsavedEdits() const override;

    void onSaveFocus() override;
    void onRestoreFocus() override;

private:
    // Top-to-bottom focus order (no hands-off: this is a per-game override, not a mode).
    enum FocusTarget { FocusList = 0, FocusApply = 1 };

    void rebuild(const rapidjson::Value& result);
    void setFocusTarget(const int target);
    void moveFocus(const int target);
    void followFocus();
    void apply();
    bool isDirty() const; // staged reorder differs from mBaselineOrder

    std::string mNs;
    std::string mTitleId;
    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mNote;
    std::shared_ptr<MadReorderList> mList;
    std::shared_ptr<ButtonComponent> mApplyButton;
    // Reorder list works in display labels; map each back to its pad identity (vid:pid)
    // so Apply sends the ordered class keys, not the labels.
    std::map<std::string, std::string> mIdByLabel;
    // Order captured at load (in the list's label space); dirty = mList->items() != this.
    std::vector<std::string> mBaselineOrder;

    int mFocusTarget;
    float mScrollCookie;
    bool mBuilt;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PERGAME_PADS_H
