//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageSidebar.h
//
//  MAD control panel: "Sidebar" section (deck-patches). Reorder every sidebar entry
//  (carry-mode list) and show/hide each one (X cycles Auto/Show/Hide); Apply persists
//  the choices (sidebar.set_order + sidebar.set) and rebuilds the live sidebar at once
//  (GuiMadPanel::refreshSidebarLive) — no panel reopen. The "sidebar" entry itself can
//  be reordered but never hidden (the always-available escape hatch). Capability rows
//  (Lightgun/X-Arcade/Bezel) still auto-hide under "Auto".
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SIDEBAR_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SIDEBAR_H

#include "components/ButtonComponent.h"
#include "components/TextComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadReorderList.h"
#include "guis/mad/widgets/MadScrollView.h"

#include <map>
#include <memory>
#include <string>
#include <vector>

class GuiMadPanel;

class GuiMadPageSidebar : public MadPage
{
public:
    GuiMadPageSidebar(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    bool onBackPressed() override; // B cancels a reorder carry first.
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;

private:
    enum FocusTarget { FocusList = 0, FocusApply = 1 };

    void requestSections();
    void populate(const rapidjson::Value& result);
    void cycleMode(int index);                  // X on a row -> auto/show/hide
    bool visibleFor(const std::string& key) const;
    void apply();
    void setFocusTarget(int target);
    void moveFocus(int target);
    void followFocus();

    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<MadReorderList> mList;
    std::shared_ptr<ButtonComponent> mApplyButton;

    std::map<std::string, std::string> mKeyByLabel;  // list label -> section key
    std::map<std::string, std::string> mMode;        // key -> pending auto|show|hide
    std::map<std::string, std::string> mInitialMode; // key -> mode at load (diff for Apply)
    std::map<std::string, bool> mCore;               // key -> is a core section
    std::map<std::string, bool> mCap;                // key -> capability currently met

    int mFocusTarget {FocusList};
    float mScrollCookie {0.0f};
    bool mBuilt {false};
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SIDEBAR_H
