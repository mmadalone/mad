//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageLindberghPadMap.h
//
//  MAD control panel: map ONE controller's buttons for a Sega Lindbergh game, slot-agnostic
//  (deck-patches). Reached from GuiMadPageLindberghPads. Same focus-row / A=bind / X=clear
//  flow as the input binder, but each press is captured ON this controller and saved to its
//  per-game profile (lindbergh.pad_load / pad_bind / pad_clear; <game>/lindbergh-pads.json).
//  At launch the materializer turns the connected controllers' profiles into the ini's
//  PLAYER_N bindings in priority order. Immediate save (no Save/Cancel).
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_LINDBERGH_PAD_MAP_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_LINDBERGH_PAD_MAP_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scaffolding.

#include <map>
#include <string>
#include <vector>

class GuiMadPanel;

class GuiMadPageLindberghPadMap : public MadLightgunPageBase
{
public:
    GuiMadPageLindberghPadMap(GuiMadPanel* panel, const std::string& title,
                              const std::string& titleid, const std::string& tag,
                              const std::string& padName);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onChildPopped() override {}

private:
    struct Row {
        std::string key;     // a JVS control, slot-agnostic (e.g. BUTTON_1, BUTTON_UP, ANALOG_1)
        std::string label;   // friendly name + JVS function (e.g. "Button 3 (Shift Up)")
        std::string display; // current codename, or "— unbound"
        std::string kind;    // "button" | "direction" | "analog" — drives the bind prompt
        bool warn {false};
        bool axis {false};   // true = analog (bind by MOVING, not pressing)
    };

    void load();
    void parse(const rapidjson::Value& result);
    void relayout();
    void applyRowUpdate(const rapidjson::Value& row);
    void bindControl(const std::string& key);
    void clearControl(const std::string& key);
    std::string rowText(const Row& row) const;

    std::string mTitleId;
    std::string mTag;
    std::string mPadName;
    std::string mCaption;
    std::map<std::string, std::vector<std::string>> mSections; // group name -> control keys
    std::map<std::string, Row> mRows;       // key -> row
    std::vector<std::string> mControlActions; // mControls index -> control key ("" = not a row)
    bool mBinding {false};
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_LINDBERGH_PAD_MAP_H
