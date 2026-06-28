//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageLindberghPads.h
//
//  MAD control panel: per-game "pads -> players" for NON-lightgun Sega Lindbergh games
//  (deck-patches). Lists the controllers in player-priority order; each opens its own
//  control map (GuiMadPageLindberghPadMap), and "Make Player 1" reorders. At launch the
//  top CONNECTED controllers fill the player slots, each using its own saved bindings, so
//  a missing controller never needs reconfiguring (the next one takes the slot). Data:
//  lindbergh.pads_get / lindbergh.pads_set_order; profiles live in <game>/lindbergh-pads.json.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_LINDBERGH_PADS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_LINDBERGH_PADS_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scaffolding.

#include <string>
#include <vector>

class GuiMadPanel;

class GuiMadPageLindberghPads : public MadLightgunPageBase
{
public:
    GuiMadPageLindberghPads(GuiMadPanel* panel, const std::string& title,
                            const std::string& titleid);

    void build() override;
    void onChildPopped() override; // returning from a pad map / reorder may change state -> reload
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct Pad {
        std::string tag;   // loader device tag (the [EVDEV] token prefix)
        std::string label; // friendly name (e.g. "Xbox 360 Wireless Receiver #1")
        bool connected {false};
        bool mapped {false};
    };

    void load();
    void parse(const rapidjson::Value& result);
    void relayout();
    void promote(const std::string& tag); // move a controller to Player 1

    std::string mTitleId;
    std::string mCaption;
    int mPlayers {2};
    std::vector<Pad> mPads; // in player-priority order
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_LINDBERGH_PADS_H
