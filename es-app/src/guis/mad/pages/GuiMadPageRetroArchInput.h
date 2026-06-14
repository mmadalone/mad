//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRetroArchInput.h
//
//  MAD control panel: RetroArch keybindings (deck-patches). Grouped input binds
//  (face / d-pad / shoulders / sticks / start-select / system hotkeys / lightgun)
//  for a selectable player, captured via the existing button-capture modal and
//  written to retroarch.cfg. Stick/gun binds (axis + mouse/keyboard events) are
//  shown read-only for now — they need a separate capture path (B4). A "Start
//  Sinden guns" button brings the guns up so they can later be mapped.
//  Backend: retroarch.input_get / retroarch.input_set / sinden.driver.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETROARCH_INPUT_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETROARCH_INPUT_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scaffolding.

#include <string>

class GuiMadPageRetroArchInput : public MadLightgunPageBase
{
public:
    GuiMadPageRetroArchInput(GuiMadPanel* panel);

    void build() override;
    void onChildPopped() override {}
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void populate(const rapidjson::Value& result);
    void captureBind(const std::string& key, const std::string& label);
    void setBind(const std::string& key, const std::string& value, const std::string& label);

    int mPlayer {1};
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETROARCH_INPUT_H
