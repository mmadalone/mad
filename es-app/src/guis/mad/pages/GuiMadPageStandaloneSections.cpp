//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageStandaloneSections.cpp
//
//  MAD control panel: Standalones sub-chooser + the shared target opener (deck-patches).
//

#include "guis/mad/pages/GuiMadPageStandaloneSections.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendDetail
#include "guis/mad/pages/GuiMadPageBezelProject.h"
#include "guis/mad/pages/GuiMadPageDaphne.h"
#include "guis/mad/pages/GuiMadPageDeviceBlacklist.h"
#include "guis/mad/pages/GuiMadPageEmuInputMap.h"
#include "guis/mad/pages/GuiMadPageEmuSettings.h"
#include "guis/mad/pages/GuiMadPageGamePicker.h"
#include "guis/mad/pages/GuiMadPageLindbergh.h"
#include "guis/mad/pages/GuiMadPageModel2.h"
#include "guis/mad/pages/GuiMadPagePadsPriority.h"
#include "guis/mad/pages/GuiMadPagePergamePads.h"
#include "guis/mad/pages/GuiMadPageRAControllers.h"
#include "guis/mad/pages/GuiMadPageRetroArchInput.h"

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
    else if (kind == "input_pergame_menu" && !arg.empty())
        // per-game input: pick a game, then a sub-chooser [Controllers, Mappings] for it.
        panel->pushPage(new GuiMadPageGamePicker(panel, title, arg, "inputmenu"));
    else if (kind == "retroarch_input")
        panel->pushPage(new GuiMadPageRetroArchInput(panel));
    else if (kind == "bezels")
        panel->pushPage(new GuiMadPageBezelProject(panel));
    else if (kind == "racontrollers")
        panel->pushPage(new GuiMadPageRAControllers(panel, title));
}

GuiMadPageStandaloneSections::GuiMadPageStandaloneSections(
    GuiMadPanel* panel, const std::string& title, const std::vector<Section>& sections)
    : MadLightgunPageBase {panel, title}
    , mSections {sections}
{
}

GuiMadPageStandaloneSections::GuiMadPageStandaloneSections(GuiMadPanel* panel, Fetch,
                                                           const std::string& listMethod,
                                                           const std::string& title)
    : MadLightgunPageBase {panel, title}
    , mListMethod {listMethod}
    , mFetch {true}
{
}

std::vector<GuiMadPageStandaloneSections::Section>
GuiMadPageStandaloneSections::parseSections(const rapidjson::Value& arr)
{
    std::vector<Section> secs;
    if (!arr.IsArray())
        return secs;
    for (rapidjson::SizeType j {0}; j < arr.Size(); ++j) {
        const rapidjson::Value& sv {arr[j]};
        Section sec;
        sec.label = MadJson::getString(sv, "label");
        sec.sublabel = MadJson::getString(sv, "sublabel");
        sec.kind = MadJson::getString(sv, "kind");
        sec.arg = MadJson::getString(sv, "arg");
        sec.title = MadJson::getString(sv, "title");
        sec.ctxVal = MadJson::getString(sv, "ctxVal");
        sec.subsections = parseSections(MadJson::getMember(sv, "sections"));
        secs.push_back(sec);
    }
    return secs;
}

void GuiMadPageStandaloneSections::build()
{
    if (!mFetch) {
        buildColumn();
        return;
    }
    setLoadingText("Loading RetroArch...");
    pageRequest(mListMethod, nullptr, [this](bool ok, const rapidjson::Value& payload) {
        setLoadingText("");
        if (!ok) {
            footer()->setStatus("Couldn't load RetroArch: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                true);
            return;
        }
        const rapidjson::Value& arr {MadJson::getMember(payload, "tiles")};
        if (arr.IsArray() && arr.Size() > 0)
            mSections = parseSections(MadJson::getMember(arr[0], "sections"));
        if (mSections.empty()) {
            setLoadingText("RetroArch isn't set up on this device.");
            return;
        }
        buildColumn();
    });
}

void GuiMadPageStandaloneSections::buildColumn()
{
    beginColumn();
    addBlock("Choose what to configure.", FONT_SIZE_SMALL,
             MadTheme::color(MadColor::Secondary),
             Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f);
    for (const Section& s : mSections) {
        const std::string label {s.sublabel.empty() ? s.label
                                                     : s.label + "  —  " + s.sublabel};
        if (s.kind == "group") {
            // A group row opens a SUB-MENU (another chooser) of its subsections.
            const std::vector<Section> subs {s.subsections};
            const std::string title {s.title};
            addButton(label, [this, subs, title] {
                mPanel->pushPage(new GuiMadPageStandaloneSections(mPanel, title, subs));
            });
            continue;
        }
        if (s.kind == "pergame_pads") {
            // Reached from the per-game input sub-menu: open the pads -> players page for the
            // already-picked game (titleid in ctxVal), no second game picker.
            const std::string arg {s.arg}, title {s.title}, tid {s.ctxVal};
            addButton(label, [this, arg, title, tid] {
                mPanel->pushPage(new GuiMadPagePergamePads(mPanel, title, arg, tid));
            });
            continue;
        }
        if (s.kind == "pergame_input") {
            const std::string arg {s.arg}, title {s.title}, tid {s.ctxVal};
            addButton(label, [this, arg, title, tid] {
                mPanel->pushPage(new GuiMadPageEmuInputMap(mPanel, title, arg, "titleid", tid));
            });
            continue;
        }
        const std::string kind {s.kind};
        const std::string arg {s.arg};
        const std::string title {s.title};
        addButton(label, [this, kind, arg, title] {
            madOpenStandaloneTarget(mPanel, kind, arg, title);
        });
    }
    endColumn();
}
