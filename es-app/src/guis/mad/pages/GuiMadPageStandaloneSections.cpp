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
#include "Settings.h"
#include "guis/mad/pages/GuiMadPageEmuInputMap.h"
#include "guis/mad/pages/GuiMadPageEmuSettings.h"
#include "guis/mad/pages/GuiMadPageGamePicker.h"
#include "guis/mad/pages/GuiMadPagePergameBrowser.h"
#include "guis/mad/pages/GuiMadPageLindbergh.h"
#include "guis/mad/pages/GuiMadPageModel2.h"
#include "guis/mad/pages/GuiMadPagePadsPriority.h"
#include "guis/mad/pages/GuiMadPagePergamePads.h"
#include "guis/mad/pages/GuiMadPagePriority.h" // GuiMadPagePriorityEdit
#include "guis/mad/pages/GuiMadPageRAControllers.h"
#include "guis/mad/pages/GuiMadPageRetroArchInput.h"
#include "guis/mad/pages/GuiMadPageRetroArchSystems.h"
#include "guis/mad/pages/GuiMadPageStandalones.h" // "grid" section -> icon-tile sub-grid

#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>

namespace
{
    // The per-game input / controllers / pads pickers use the RA-style media+info
    // browser only when the UI setting opts all per-game pages in; the settings
    // picker always uses it. See Settings "MadPergameBrowserScope".
    bool madPergameBrowserForInput()
    {
        return Settings::getInstance()->getString("MadPergameBrowserScope") == "all";
    }
} // namespace

void madOpenStandaloneTarget(GuiMadPanel* panel, const std::string& kind,
                             const std::string& arg, const std::string& title,
                             const std::string& context)
{
    if (kind == "settings" && !arg.empty())
        panel->pushPage(new GuiMadPageEmuSettings(panel, title, arg));
    else if (kind == "settings_pergame" && !arg.empty())
        // The settings picker ALWAYS uses the media+info browser (mSystem comes
        // from the <ns>.games payload).
        panel->pushPage(new GuiMadPagePergameBrowser(panel, title, arg, "", "settings"));
    else if (kind == "input_pergame" && !arg.empty()) {
        if (madPergameBrowserForInput())
            panel->pushPage(new GuiMadPagePergameBrowser(panel, title, arg, "", "input", {}, context));
        else
            panel->pushPage(new GuiMadPageGamePicker(panel, title, arg, "input", {}, context));
    }
    else if (kind == "pads_pergame" && !arg.empty()) {
        if (madPergameBrowserForInput())
            panel->pushPage(new GuiMadPagePergameBrowser(panel, title, arg, "", "pads"));
        else
            panel->pushPage(new GuiMadPageGamePicker(panel, title, arg, "pads"));
    }
    else if (kind == "input_map" && !arg.empty())
        panel->pushPage(new GuiMadPageEmuInputMap(panel, title, arg, "", "", context));
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
    else if (kind == "lindbergh_pads") {
        if (madPergameBrowserForInput())
            panel->pushPage(new GuiMadPagePergameBrowser(panel, title, "lindbergh", "", "pads"));
        else
            panel->pushPage(new GuiMadPageGamePicker(panel, title, "lindbergh", "pads"));
    }
    else if (kind == "input_pergame_menu" && !arg.empty()) {
        // per-game input: pick a game, then a sub-chooser [Controllers, Mappings] for it.
        if (madPergameBrowserForInput())
            panel->pushPage(new GuiMadPagePergameBrowser(panel, title, arg, "", "inputmenu"));
        else
            panel->pushPage(new GuiMadPageGamePicker(panel, title, arg, "inputmenu"));
    }
    else if (kind == "retroarch_input")
        panel->pushPage(new GuiMadPageRetroArchInput(panel));
    else if (kind == "bezels")
        panel->pushPage(new GuiMadPageBezelProject(panel));
    else if (kind == "racontrollers")
        panel->pushPage(new GuiMadPageRAControllers(panel, title));
    else if (kind == "ra_systems")
        panel->pushPage(new GuiMadPageRetroArchSystems(panel, title));
    else if (kind == "ra_systems_handheld")
        // On-the-go per-game HANDHELD input: same systems grid, but each game jumps to its handheld
        // input editor (ragamehh) instead of the permanent per-game menu.
        panel->pushPage(new GuiMadPageRetroArchSystems(panel, title, true));
    else if (kind == "priority_scopes")
        // The two-grid per-system + collection controller-rules page, now the
        // RetroArch hub "Per-system settings" section.
        panel->pushPage(new GuiMadPagePriority(panel));
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
        sec.context = MadJson::getString(sv, "context");
        sec.key = MadJson::getString(sv, "key");
        sec.note = MadJson::getString(sv, "note");
        sec.subsections = parseSections(MadJson::getMember(sv, "sections"));
        if (sec.kind == "grid") {
            // A grid section's subsections ARE its tiles (each with its own art/sections). The
            // Section struct drops "art", so keep the raw array as a {"tiles":[...]} payload for a
            // GuiMadPageStandalones sub-grid, which renders + routes each tile itself.
            const rapidjson::Value& tilesArr {MadJson::getMember(sv, "sections")};
            if (tilesArr.IsArray()) {
                rapidjson::StringBuffer buf;
                rapidjson::Writer<rapidjson::StringBuffer> writer {buf};
                writer.StartObject();
                writer.Key("tiles");
                tilesArr.Accept(writer);
                writer.EndObject();
                sec.tilesJson = buf.GetString();
            }
        }
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
    setLoadingText("Loading...");
    pageRequest(mListMethod, nullptr, [this](bool ok, const rapidjson::Value& payload) {
        setLoadingText("");
        if (!ok) {
            footer()->setStatus("Couldn't load this section: " +
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
    // Vertical section rows hug their own label: pass minWidth 0 so a short label ("Wii") is not
    // centered inside the shared "DELETE"-width floor, which otherwise reads as a leading gap.
    auto colButton = [this](const std::string& lbl, const std::function<void()>& cb) {
        auto b = addButton(lbl, cb);
        b->setMinWidth(0.0f);
        return b;
    };
    for (const Section& s : mSections) {
        const std::string label {s.sublabel.empty() ? s.label
                                                     : s.label + "  —  " + s.sublabel};
        if (s.kind == "group") {
            // A group row opens a SUB-MENU (another chooser) of its subsections.
            const std::vector<Section> subs {s.subsections};
            const std::string title {s.title};
            colButton(label, [this, subs, title] {
                mPanel->pushPage(new GuiMadPageStandaloneSections(mPanel, title, subs));
            });
            continue;
        }
        if (s.kind == "grid") {
            // A grid row opens an icon-tile SUB-GRID (reuses the Standalones tile grid + routing):
            // each tile carries its own art + sections, so a 1-section tile opens its page directly
            // and a multi-section tile opens its own [Settings, ...] chooser.
            const std::string json {s.tilesJson};
            const std::string title {s.title};
            const std::string note {s.note};
            colButton(label, [this, json, title, note] {
                mPanel->pushPage(new GuiMadPageStandalones(mPanel, title, json, note));
            });
            continue;
        }
        if (s.kind == "settings_pergame_menu") {
            // Pick a game, then open a sub-menu of its per-game pages (Add-Ons / Cheats /
            // System / … / Input Profiles / Linux), each carrying the picked titleid. The leaf
            // rows come from the server (s.subsections); the picker injects the titleid on pick.
            const std::string arg {s.arg}, title {s.title};
            const std::vector<Section> leaves {s.subsections};
            colButton(label, [this, arg, title, leaves] {
                // Settings picker always uses the media+info browser.
                mPanel->pushPage(
                    new GuiMadPagePergameBrowser(mPanel, title, arg, "", "settingsmenu", leaves));
            });
            continue;
        }
        if (s.kind == "pergame_pads") {
            // Reached from the per-game input sub-menu: open the pads -> players page for the
            // already-picked game (titleid in ctxVal), no second game picker.
            const std::string arg {s.arg}, title {s.title}, tid {s.ctxVal};
            colButton(label, [this, arg, title, tid] {
                mPanel->pushPage(new GuiMadPagePergamePads(mPanel, title, arg, tid));
            });
            continue;
        }
        if (s.kind == "pergame_input") {
            const std::string arg {s.arg}, title {s.title}, tid {s.ctxVal}, context {s.context};
            colButton(label, [this, arg, title, tid, context] {
                mPanel->pushPage(new GuiMadPageEmuInputMap(mPanel, title, arg, "titleid", tid, context));
            });
            continue;
        }
        if (s.kind == "pergame_settings") {
            // RetroArch per-game Settings/Input-remap: the generic groups-driven
            // editor targeting ns="ragameset"/"ragamein" via a "titleid" context
            // ("<system>:<stem>", already picked — GuiMadPageRetroArchGame).
            const std::string arg {s.arg}, title {s.title}, tid {s.ctxVal}, core {s.core};
            colButton(label, [this, arg, title, tid, core] {
                mPanel->pushPage(new GuiMadPageEmuSettings(mPanel, title, arg, "titleid", tid, core));
            });
            continue;
        }
        if (s.kind == "pergame_priority") {
            // RetroArch per-game Controllers: reuse the scope-agnostic priority
            // editor with kind="game" (priority.get/policy.set_ports already
            // accept it) — ctxVal carries the "<system>:<stem>" identity. title
            // is the clean per-game header GuiMadPageRetroArchGame already built
            // ("<Game Name> — Controllers"), passed through as the display
            // title so the page doesn't fall back to a raw titleid uppercased.
            const std::string tid {s.ctxVal};
            const std::string title {s.title};
            colButton(label, [this, tid, title] {
                mPanel->pushPage(new GuiMadPagePriorityEdit(mPanel, tid, "game", title));
            });
            continue;
        }
        const std::string kind {s.kind};
        const std::string arg {s.arg};
        const std::string title {s.title};
        const std::string context {s.context};
        colButton(label, [this, kind, arg, title, context] {
            madOpenStandaloneTarget(mPanel, kind, arg, title, context);
        });
    }
    endColumn();
}
