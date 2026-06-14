//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBezelProject.h
//
//  MAD control panel: Bezel Project page (deck-patches). A console-art tile grid
//  of bezel packs; picking one opens a detail page to install / remove / enable /
//  disable that system's RetroArch bezels. Backend: bezels.* (lib/bezel_cfg.py).
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_PROJECT_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_PROJECT_H

#include "components/TextComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase (detail page)
#include "guis/mad/widgets/MadScrollView.h"
#include "guis/mad/widgets/MadTileGrid.h"

#include <map>
#include <string>
#include <vector>

class GuiMadPageBezelProject : public MadPage
{
public:
    GuiMadPageBezelProject(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override;

private:
    void rebuild(const rapidjson::Value& result);
    void followFocus();

    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<MadTileGrid> mGrid;
    std::map<std::string, std::string> mLabelByKey;
    int mGridCookie;
    float mScrollCookie;
};

// Per-system detail: status + Install / Remove / Enable all / Disable all.
class GuiMadPageBezelDetail : public MadLightgunPageBase
{
public:
    GuiMadPageBezelDetail(GuiMadPanel* panel, const std::string& key, const std::string& label);

    void build() override;
    void onChildPopped() override {}

private:
    void rebuild(const rapidjson::Value& status);
    void action(const std::string& method, const std::string& doing, int timeoutMs);

    std::string mKey;
    std::string mLabel;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_PROJECT_H
