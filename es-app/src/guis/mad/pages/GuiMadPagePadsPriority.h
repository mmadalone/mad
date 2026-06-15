//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePadsPriority.h
//
//  MAD control panel: per-emulator "Controllers → pads → players" (deck-patches).
//  Opened from a Standalones tile's "Controllers" section for the Switch
//  emulators (Eden / Ryujinx). Lists the connected pads in a carry-mode reorder
//  list (top = Player 1); Apply resolves the top-N connected pads to player slots
//  and writes that emulator's own config device bindings (preserving per-button
//  remaps) — configure-once, no router/launch-time involvement. Data: pads.get /
//  pads.set (arg = emulator key).
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PADS_PRIORITY_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PADS_PRIORITY_H

#include "components/ButtonComponent.h"
#include "components/TextComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadReorderList.h"
#include "guis/mad/widgets/MadScrollView.h"

#include <map>
#include <string>
#include <vector>

class GuiMadPagePadsPriority : public MadPage
{
public:
    GuiMadPagePadsPriority(GuiMadPanel* panel, const std::string& title,
                           const std::string& emu);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    bool onBackPressed() override; // B cancels a reorder carry first.
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;

private:
    // Top-to-bottom focus order. HandsOff is always present; List/Apply only when
    // MAD manages this emulator (hands-off OFF) and pads are connected.
    enum FocusTarget { FocusHandsOff = 0, FocusList = 1, FocusApply = 2 };

    void rebuild(const rapidjson::Value& result);
    void setFocusTarget(const int target);
    void moveFocus(const int target);
    void followFocus();
    void apply();
    void toggleHandsOff();

    std::string mEmu;
    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<ButtonComponent> mHandsOffButton;
    std::shared_ptr<TextComponent> mNote;
    std::shared_ptr<MadReorderList> mList;
    std::shared_ptr<ButtonComponent> mApplyButton;
    bool mHandsOff {false};
    // Reorder list works in display labels; map each back to its pad identity
    // (vid:pid) so Apply can send the stored-order keys, not the labels.
    std::map<std::string, std::string> mIdByLabel;

    int mPlayers;
    int mFocusTarget;
    int mListCookie;
    float mScrollCookie;
    bool mBuilt;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PADS_PRIORITY_H
