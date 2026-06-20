//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBezelPerGame.h
//
//  MAD control panel: per-game bezel toggles for one system (deck-patches). A
//  virtualized list of the system's configured games, each toggleable on/off, with
//  the focused game's bezel image previewed on the right. Y opens ES-DE's on-screen
//  keyboard to filter. The full list shows with no cap (MadVirtualList only builds
//  the on-screen rows). Backend: bezels.games / bezels.disable_game.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_PER_GAME_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_PER_GAME_H

#include "components/ImageComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadVirtualList.h"

#include <functional>
#include <string>
#include <vector>

class GuiMadPageBezelPerGame : public MadPage
{
public:
    GuiMadPageBezelPerGame(GuiMadPanel* panel, const std::string& key, const std::string& label,
                           const std::function<void()>& onChanged = nullptr);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override {}
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct Game {
        std::string name;  // rom stem — the WRITE key (bezels.disable_game); never shown raw if a title exists
        bool enabled;
        std::string preview;
        std::string title; // gamelist <name>; "" -> fall back to the stem for display
    };
    // Display text for a row: the human title when present, else the rom stem.
    static std::string rowText(const Game& g) { return g.title.empty() ? g.name : g.title; }
    static unsigned int rowColor(const bool enabled); // enabled = primary, disabled = dimmed

    void ensureWidgets();  // create the header / list / preview once
    void populate();       // (re)build the filtered list + preview pane
    void updatePreview();  // show the focused game's bezel
    void openSearch();
    void toggleGame(int i); // flip one game's bezel + relabel its row in place

    std::string mKey;
    std::string mLabel;
    std::string mFilter;
    std::vector<Game> mGames; // all games for the system
    std::vector<Game> mShown; // filtered subset, parallel to the list rows
    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    std::shared_ptr<ImageComponent> mPreview;
    std::function<void()> mOnChanged; // notify the detail page a toggle happened
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_PER_GAME_H
