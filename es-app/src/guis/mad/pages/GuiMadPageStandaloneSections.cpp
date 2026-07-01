//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageStandaloneSections.cpp
//
//  MAD control panel: Standalones sub-chooser + the shared target opener (deck-patches).
//

#include "guis/mad/pages/GuiMadPageStandaloneSections.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendDetail
#include "guis/mad/pages/GuiMadPageDaphne.h"
#include "guis/mad/pages/GuiMadPageDeviceBlacklist.h"
#include "guis/mad/pages/GuiMadPageEmuInputMap.h"
#include "guis/mad/pages/GuiMadPageEmuSettings.h"
#include "guis/mad/pages/GuiMadPageGamePicker.h"
#include "guis/mad/pages/GuiMadPageLindbergh.h"
#include "guis/mad/pages/GuiMadPageModel2.h"
#include "guis/mad/pages/GuiMadPagePadsPriority.h"

void madOpenStandaloneTarget(GuiMadPanel* panel, const std::string& kind,
                             const std::string& arg, const std::string& title)
{
    if (kind == "settings" && !arg.empty())
        panel->pushPage(new GuiMadPageEmuSettings(panel, title, arg));
    else if (kind == "settings_pergame" && !arg.empty())
        panel->pushPage(new GuiMadPageGamePicker(panel, title, arg));
    else if (kind == "input_pergame" && !arg.empty())
        panel->pushPage(new GuiMadPageGamePicker(panel, title, arg, "input"));
    else if (kind == "pads_pergame" && !arg.empty())
        panel->pushPage(new GuiMadPageGamePicker(panel, title, arg, "pads"));
    else if (kind == "input_map" && !arg.empty())
        panel->pushPage(new GuiMadPageEmuInputMap(panel, title, arg));
    else if (kind == "pads_map" && !arg.empty())
        panel->pushPage(new GuiMadPagePadsPriority(panel, title, arg));
    else if (kind == "pads_hide" && !arg.empty())
        panel->pushPage(new GuiMadPageDeviceBlacklist(panel, title, arg));
    else if (kind == "gamepad" && !arg.empty())
        panel->pushPage(new GuiMadPageBackendDetail(panel, arg));
    else if (kind == "model2")
        panel->pushPage(new GuiMadPageModel2(panel));
    else if (kind == "daphne_map")
        panel->pushPage(new GuiMadPageDaphne(panel));
    else if (kind == "lindbergh_map")
        panel->pushPage(new GuiMadPageLindbergh(panel));
    else if (kind == "lindbergh_pads")
        panel->pushPage(new GuiMadPageGamePicker(panel, title, "lindbergh", "pads"));
}

GuiMadPageStandaloneSections::GuiMadPageStandaloneSections(
    GuiMadPanel* panel, const std::string& title, const std::vector<Section>& sections)
    : MadLightgunPageBase {panel, title}
    , mSections {sections}
{
}

void GuiMadPageStandaloneSections::build()
{
    beginColumn();
    addBlock("Choose what to configure.", FONT_SIZE_SMALL,
             MadTheme::color(MadColor::Secondary),
             Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f);
    for (const Section& s : mSections) {
        const std::string label {s.sublabel.empty() ? s.label
                                                     : s.label + "  —  " + s.sublabel};
        const std::string kind {s.kind};
        const std::string arg {s.arg};
        const std::string title {s.title};
        addButton(label, [this, kind, arg, title] {
            madOpenStandaloneTarget(mPanel, kind, arg, title);
        });
    }
    endColumn();
}
