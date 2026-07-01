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
                             const std::string& arg, const std::string& title);

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
        std::vector<Section> subsections; // kind "group": the sub-menu rows it opens
    };

    GuiMadPageStandaloneSections(GuiMadPanel* panel, const std::string& title,
                                 const std::vector<Section>& sections);

    void build() override;
    void onChildPopped() override {}

private:
    std::vector<Section> mSections;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_STANDALONE_SECTIONS_H
