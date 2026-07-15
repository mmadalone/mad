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
#include "guis/mad/pages/GuiMadPageLindberghPads.h"
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

void GuiMadPageStandaloneSections::openLeaf(GuiMadPanel* panel, const Section& s)
{
    // The per-game kinds carry the picked game's titleid in ctxVal (and RA settings a core), which
    // the free madOpenStandaloneTarget does not take -- so they are dispatched here, mirroring the
    // buildColumn() row handlers exactly. Any other kind falls through to the free opener.
    if (s.kind == "pergame_settings")
        panel->pushPage(new GuiMadPageEmuSettings(panel, s.title, s.arg, "titleid", s.ctxVal, s.core));
    else if (s.kind == "pergame_pads")
        panel->pushPage(new GuiMadPagePergamePads(panel, s.title, s.arg, s.ctxVal));
    else if (s.kind == "pergame_input")
        panel->pushPage(
            new GuiMadPageEmuInputMap(panel, s.title, s.arg, "titleid", s.ctxVal, s.context));
    else if (s.kind == "pergame_priority")
        panel->pushPage(new GuiMadPagePriorityEdit(panel, s.ctxVal, "game", s.title));
    else if (s.kind == "pergame_lindbergh_pads")
        panel->pushPage(new GuiMadPageLindberghPads(panel, s.title, s.ctxVal));
    else if (s.kind == "pergame_lindbergh_map")
        panel->pushPage(new GuiMadPageLindbergh(panel, s.title, s.ctxVal));
    else
        madOpenStandaloneTarget(panel, s.kind, s.arg, s.title, s.context);
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
        sec.core = MadJson::getString(sv, "core");
        sec.key = MadJson::getString(sv, "key");
        sec.value = MadJson::getBool(sv, "value", false);
        sec.note = MadJson::getString(sv, "note");
        {
            // Tile art (theme-resolved). The server sends "art":[path] (first wins); a menu rendered
            // as a grid uses it, a menu rendered as a list ignores it.
            const rapidjson::Value& artArr {MadJson::getMember(sv, "art")};
            if (artArr.IsArray() && artArr.Size() > 0 && artArr[0].IsString())
                sec.art = artArr[0].GetString();
        }
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

namespace
{
    using JsonWriter = rapidjson::Writer<rapidjson::StringBuffer>;
    using SecT = GuiMadPageStandaloneSections::Section;

    void jStr(JsonWriter& w, const std::string& v)
    {
        w.String(v.c_str(), static_cast<rapidjson::SizeType>(v.size()));
    }
    void jField(JsonWriter& w, const char* key, const std::string& v)
    {
        if (v.empty())
            return;
        w.Key(key);
        jStr(w, v);
    }

    // One leaf section object for a tile's "sections":[...] -- kind + the dispatch payload the grid's
    // single-section collapse feeds to openLeaf(). (Groups never reach here; they become "members".)
    void writeLeafSection(JsonWriter& w, const SecT& s)
    {
        w.StartObject();
        jField(w, "label", s.label);
        jField(w, "kind", s.kind);
        jField(w, "arg", s.arg);
        jField(w, "title", s.title);
        jField(w, "ctxVal", s.ctxVal);
        jField(w, "context", s.context);
        jField(w, "core", s.core);
        jField(w, "key", s.key);
        w.EndObject();
    }

    void writeTile(JsonWriter& w, const SecT& s);

    void writeTiles(JsonWriter& w, const std::vector<SecT>& secs)
    {
        w.StartArray();
        for (const SecT& s : secs)
            writeTile(w, s);
        w.EndArray();
    }

    void writeTile(JsonWriter& w, const SecT& s)
    {
        // A 1-child group collapses to its child: a single-tile grid is a wasted navigation step.
        // KEEP this group's label + art (memory mad-collapse-single-child-groups; matches the Python
        // _collapse_singletons convention) so the collapsed tile is not confusable with a same-named
        // sibling -- e.g. a Cemu game with packs in only the Graphics category would otherwise render
        // a second bare "Graphics" tile next to the top-level Graphics settings leaf. Recurse so a
        // chain of 1-child groups fully collapses, the outermost label preserved throughout.
        if (s.kind == "group" && s.subsections.size() == 1) {
            SecT child {s.subsections.front()};
            child.label = s.label;
            if (!s.art.empty())
                child.art = s.art;
            writeTile(w, child);
            return;
        }
        w.StartObject();
        w.Key("key");
        jStr(w, s.key.empty() ? s.label : s.key);
        jField(w, "label", s.label);
        jField(w, "sublabel", s.sublabel); // hint text under the tile label (e.g. "which pad is each player")
        if (!s.art.empty()) {
            w.Key("art");
            w.StartArray();
            jStr(w, s.art);
            w.EndArray();
        }
        if (s.kind == "group") {
            jField(w, "title", s.title); // game-qualified header ("<game> - System") for the sub-grid page
            w.Key("members");
            writeTiles(w, s.subsections);
        }
        else {
            w.Key("sections");
            w.StartArray();
            writeLeafSection(w, s);
            w.EndArray();
        }
        w.EndObject();
    }
} // namespace

std::string GuiMadPageStandaloneSections::sectionsToTilesJson(const std::vector<Section>& sections)
{
    rapidjson::StringBuffer buf;
    JsonWriter w {buf};
    w.StartObject();
    w.Key("tiles");
    writeTiles(w, sections);
    w.EndObject();
    return buf.GetString();
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
        // The per-game leaf kinds (pergame_pads / pergame_input / pergame_lindbergh_pads /
        // pergame_lindbergh_map / pergame_settings / pergame_priority) are no longer handled here:
        // per-game menus render as GRIDS now (sectionsToTilesJson), so they never reach this list
        // builder, and the single dispatch source is openLeaf (the fallthrough below).
        if (s.kind == "toggle") {
            // Inline bool toggle (the X-Arcade warning): a single chip flipped in
            // place instead of opening a one-toggle settings sub-page. A toggles it
            // optimistically and persists via <arg>.set {key, value}; a write
            // failure reverts the chip to the on-disk truth. Mirrors the bool path
            // in GuiMadPageEmuSettings (setOption + MadChipRow::setOnToggle).
            const std::string ns {s.arg};
            std::vector<MadChipRow::Chip> chips {{s.key, s.label, s.value}};
            auto row = addChips(chips, false);
            MadChipRow* raw {row.get()};
            row->setOnToggle([this, ns, raw](const std::string& key, bool on) {
                pageRequest(
                    ns + ".set",
                    [key, on](MadJson::Writer& writer) {
                        writer.Key("key");
                        writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
                        writer.Key("value");
                        writer.String(on ? "1" : "0", 1);
                    },
                    [this, raw, key, on](bool ok, const rapidjson::Value& payload) {
                        if (!ok) {
                            footer()->flash("Couldn't save X-Arcade warning: " +
                                                MadJson::getString(payload, "message",
                                                                   "unknown error"),
                                            4000, true);
                            raw->setChipState(key, !on);
                        }
                    });
            });
            continue;
        }
        // Everything else routes through openLeaf -- the SINGLE dispatch source shared with the grid
        // collapse (GuiMadPageStandalones::open). It handles the per-game kinds (which carry the
        // picked titleid in ctxVal) and falls back to the free madOpenStandaloneTarget for the rest.
        colButton(label, [this, s] { GuiMadPageStandaloneSections::openLeaf(mPanel, s); });
    }
    endColumn();
}
