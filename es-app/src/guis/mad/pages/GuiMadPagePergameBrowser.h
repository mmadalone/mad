//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePergameBrowser.h
//
//  MAD control panel: the reusable per-game media+info browser (deck-patches).
//  A two-pane page -- LEFT a virtualized game list with a "* " override badge,
//  RIGHT the highlighted game's media (art rolling into the preview video,
//  resolved straight from ES-DE's own FileData so it inherits the user's
//  MediaDirectory + gamelist settings) stacked above an info panel (the
//  per-game overrides summary). Y opens the on-screen keyboard to filter by
//  name or rom stem. A on a game opens its per-game destination.
//
//  This is a drop-in twin of GuiMadPageGamePicker: the SAME (ns, target,
//  menuSections) contract and the SAME on-select dispatch per target
//  (settings / settingsmenu / input / inputmenu / pads) -- only the rendering
//  differs (the media+info two-pane list vs a plain button column). Backend:
//  <ns>.games -> {games:[{titleid,stem,name,override,summary}], ...}.
//
//  GuiMadPageRetroArchGame derives from this and overrides the RA-specific
//  bits (per-system cores + core picker, "<system>:<stem>" identity, the fixed
//  Settings / Input remap / Controllers sub-menu) so there is one copy of the
//  two-pane layout code.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PERGAME_BROWSER_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PERGAME_BROWSER_H

#include "guis/mad/MadPage.h"
#include "guis/mad/pages/GuiMadPageStandaloneSections.h" // Section
#include "guis/mad/widgets/MadVideoComponent.h"
#include "guis/mad/widgets/MadVirtualList.h"

#include <string>
#include <unordered_map>
#include <vector>

class FileData;

class GuiMadPagePergameBrowser : public MadPage
{
public:
    // target: "settings" | "settingsmenu" | "input" | "inputmenu" | "pads" -- the same
    // vocabulary GuiMadPageGamePicker uses; the on-select dispatch matches it.
    GuiMadPagePergameBrowser(
        GuiMadPanel* panel, const std::string& title, const std::string& ns,
        const std::string& system, const std::string& target = "settings",
        const std::vector<GuiMadPageStandaloneSections::Section>& menuSections = {});

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override; // a per-game edit may flip the "* " badge; re-issue the list.
    std::vector<HelpPrompt> getHelpPrompts() override;

protected:
    struct Game {
        std::string id;      // identity passed to the destination (titleid/serial; RA: <system>:<stem>)
        std::string stem;    // rom stem -- media lookup + search
        std::string name;    // gamelist <name> -- row + preview display
        bool overrides;      // "* " row prefix + a distinct color
        std::string summary; // info-panel text ("" == no per-game overrides)
        std::string sub;     // optional subtitle line under the name (RA: "Core: <name>")
    };
    static unsigned int rowColor(bool overrides); // override = a distinct color, else Primary

    // --- hooks: the defaults implement the generic page; RA overrides these ---
    virtual void writeGamesArgs(MadJson::Writer& w);           // extra <ns>.games request args
    virtual std::string gameId(const rapidjson::Value& g);     // identity from a games[] entry
    virtual void parsePayloadExtra(const rapidjson::Value&) {} // RA: parse the sibling "cores" array
    virtual void perGameExtra(const rapidjson::Value&, Game&) {} // RA: game "core" -> sub
    virtual void openGame(int i);                              // default: dispatch by target
    virtual std::string previewHeadLines(const Game&) { return ""; } // RA: the "Edit: <core>" line
    virtual std::string defaultSummary() { return "No per-game overrides yet."; } // RA: the 3-line block
    virtual bool onExtraButton(InputConfig*, Input) { return false; } // RA: X = core picker
    virtual void extraHelpPrompts(std::vector<HelpPrompt>&) {} // RA: X = core

    void requestGames(bool keepCursor);
    void ensureWidgets();               // create the header / list / media / preview once
    void populate(bool keepCursor = false); // (re)build the filtered list + preview pane
    void updatePreview();               // LOCAL -- no RPC
    void openSearch();

    std::string mNs;
    std::string mSystem;
    std::string mTarget;
    std::vector<GuiMadPageStandaloneSections::Section> mMenuSections;
    std::string mFilter;
    std::string mNote; // empty-state guidance from the <ns>.games payload ("note")
    std::vector<Game> mGames; // all games for the system
    std::vector<Game> mShown; // filtered subset, parallel to the list rows
    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    std::shared_ptr<MadVideoComponent> mVideo; // art (embedded static image) + preview video
    std::shared_ptr<TextComponent> mPreview;
    // Media lookup indices, built once in ensureWidgets() from the live SystemData
    // tree (the <ns>.games payload stays the source of truth for the "* " badge /
    // overrides summary). mByStem keys on the ROM filename stem (getStem), matched
    // first; mByName keys on the gamelist's scraped name (getName, lowercased) as a
    // fallback so a game whose settings-identity can't yield a filename stem (e.g.
    // an untagged Switch ROM) still resolves its media by name.
    std::unordered_map<std::string, FileData*> mByStem;
    std::unordered_map<std::string, FileData*> mByName;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PERGAME_BROWSER_H
