//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageStandalones.h
//
//  MAD control panel: Standalones hub (deck-patches). One console-art tile per
//  standalone emulator; picking a tile opens that emulator's existing config page
//  (Model 2 settings, the per-emulator gamepad detail, or Daphne button mapping).
//  The tile list comes from the backend's standalones.list (filtered to systems
//  present in ES-DE, art = the system's console.png).
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_STANDALONES_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_STANDALONES_H

#include "components/TextComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/pages/GuiMadPageStandaloneSections.h" // Section
#include "guis/mad/widgets/MadScrollView.h"
#include "guis/mad/widgets/MadTileGrid.h"

#include <map>
#include <string>
#include <vector>

class GuiMadPageStandalones : public MadPage
{
public:
    GuiMadPageStandalones(GuiMadPanel* panel);
    // Sub-grid page for a GROUP tile (e.g. Switch → Eden/Ryujinx): renders a
    // provided `{"tiles":[…members…]}` payload instead of fetching
    // standalones.list. Reuses the same tile grid, so the sub-page looks like
    // the top grid (icon tiles), and each member tile opens its section chooser.
    GuiMadPageStandalones(GuiMadPanel* panel, const std::string& title,
                          const std::string& membersJson, const std::string& intro = "");

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
    void open(const std::string& key);

    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<MadTileGrid> mGrid;

    // tile key -> its config sections (+ label); onPick opens a single section
    // directly or shows a chooser for several.
    std::map<std::string, std::vector<GuiMadPageStandaloneSections::Section>> mSectionsByKey;
    std::map<std::string, std::string> mLabelByKey;
    // GROUP tile key -> its serialized members payload; onPick pushes a sub-grid.
    std::map<std::string, std::string> mGroupJsonByKey;

    // Set for a sub-grid page (constructed with a members payload): build()
    // renders mProvidedJson rather than fetching standalones.list.
    bool mIsSub {false};
    std::string mProvidedJson;
    std::string mSubIntro; // sub-grid: optional intro override (empty = the default emulator text)

    int mGridCookie;
    float mScrollCookie;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_STANDALONES_H
