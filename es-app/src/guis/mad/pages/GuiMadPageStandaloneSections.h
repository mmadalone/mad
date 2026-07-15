//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageStandaloneSections.h
//
//  MAD control panel: a small chooser shown when one standalone emulator has more
//  than one config aspect (Daphne = Button mapping + Controllers; later: per-
//  emulator Settings + Controllers). Each entry opens an existing config page via
//  madOpenStandaloneTarget(). (deck-patches)
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_STANDALONE_SECTIONS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_STANDALONE_SECTIONS_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scaffolding.

#include <string>
#include <vector>

class GuiMadPanel;

// Open a standalone emulator's config page by target kind:
//   settings   -> GuiMadPageEmuSettings(title, arg = RPC namespace)
//   gamepad    -> GuiMadPageBackendDetail(arg = backend name)
//   model2     -> GuiMadPageModel2 (settings)
//   daphne_map -> GuiMadPageDaphne (button mapping)
void madOpenStandaloneTarget(GuiMadPanel* panel, const std::string& kind,
                             const std::string& arg, const std::string& title,
                             const std::string& context = "");

class GuiMadPageStandaloneSections : public MadLightgunPageBase
{
public:
    struct Section {
        std::string label;
        std::string sublabel;
        std::string kind;
        std::string arg;    // gamepad: backend name; settings: RPC namespace
        std::string title;  // settings: the settings page title
        std::string ctxVal; // pergame_pads/pergame_input: the picked game's titleid
        std::string context; // input pages: "docked"|"handheld" launch context (empty = docked)
        std::string core;   // pergame_settings (RetroArch): optional core override; empty = all cores
        std::string key;    // per-game stable id; a leaf whose key is in the game's "hide" list is omitted
        std::string art;    // tile icon (theme-resolved path); used when a menu is rendered as a grid
        bool value {false}; // kind "toggle": the flag's current on/off state (initial chip state)
        std::vector<Section> subsections; // kind "group": the sub-menu rows it opens
        std::string tilesJson; // kind "grid": {"tiles":[...]} payload for a GuiMadPageStandalones sub-grid
        std::string note;      // kind "grid": optional intro line shown above the sub-grid
    };

    // Serialize a per-game menu's leaves into a {"tiles":[...]} payload a GuiMadPageStandalones grid
    // renders directly: a "group" leaf -> a tile with "members" (its subsections, recursively); any
    // other leaf -> a tile carrying that single section (opened via the grid's single-section
    // collapse). A group with exactly one visible child collapses to that child (a 1-tile grid is a
    // wasted step). Each tile keeps the section's "art".
    static std::string sectionsToTilesJson(const std::vector<Section>& sections);

    // Open ONE leaf section directly (the per-game kinds carry the picked titleid in ctxVal, which
    // the free madOpenStandaloneTarget does not receive). Used by the grid's single-section collapse
    // and by a per-game menu that has a single visible leaf (opened straight, no 1-tile grid).
    static void openLeaf(GuiMadPanel* panel, const Section& s);

    GuiMadPageStandaloneSections(GuiMadPanel* panel, const std::string& title,
                                 const std::vector<Section>& sections);

    // Parse a JSON "sections" array into Section rows, RECURSIVELY: a section may carry a
    // nested "sections" array (a group's sub-menu) plus a "ctxVal" (per-game titleid).
    static std::vector<Section> parseSections(const rapidjson::Value& arr);

    void build() override;
    void onChildPopped() override {}

private:
    void buildColumn();

    std::vector<Section> mSections;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_STANDALONE_SECTIONS_H
