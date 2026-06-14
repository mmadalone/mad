//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBezelPerGame.h
//
//  MAD control panel: per-game bezel toggles for one system (deck-patches). A
//  scrollable list of the system's configured games, each toggleable on/off, with
//  the focused game's bezel image previewed on the right. Y opens ES-DE's on-screen
//  keyboard to filter. Backend: bezels.games / bezels.disable_game.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_PER_GAME_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_PER_GAME_H

#include "components/ButtonComponent.h"
#include "components/ImageComponent.h"
#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scroll-list scaffolding.

#include <functional>
#include <string>
#include <vector>

class GuiMadPageBezelPerGame : public MadLightgunPageBase
{
public:
    GuiMadPageBezelPerGame(GuiMadPanel* panel, const std::string& key, const std::string& label,
                           const std::function<void()>& onChanged = nullptr);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void onChildPopped() override {}
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct Game {
        std::string name;
        bool enabled;
        std::string preview;
    };

    void populate();      // (re)build the filtered list + preview pane
    void updatePreview(); // show the focused game's bezel
    void openSearch();
    void toggleGame(int i); // flip one game's bezel + relabel its cell

    std::string mKey;
    std::string mLabel;
    std::string mFilter;
    std::vector<Game> mGames; // all games for the system
    std::vector<Game> mShown; // filtered subset, parallel to mControls / mGameButtons
    std::vector<std::shared_ptr<ButtonComponent>> mGameButtons; // one per shown game (grid cells)
    std::shared_ptr<ImageComponent> mPreview;
    std::function<void()> mOnChanged; // notify the detail page a toggle happened
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_PER_GAME_H
