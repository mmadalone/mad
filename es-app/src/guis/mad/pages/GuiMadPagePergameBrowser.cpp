//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePergameBrowser.cpp  (deck-patches)
//
//  The reusable per-game media+info browser; see the header. The two-pane
//  skeleton is shared with GuiMadPageRetroArchGame (which derives from this).
//

#include "guis/mad/pages/GuiMadPagePergameBrowser.h"

#include "FileData.h"
#include "SystemData.h"
#include "Window.h"
#include "guis/GuiTextEditKeyboardPopup.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageEmuInputMap.h"
#include "guis/mad/pages/GuiMadPageEmuSettings.h"
#include "guis/mad/pages/GuiMadPageLindberghPads.h"
#include "guis/mad/pages/GuiMadPagePergamePads.h"

#include <algorithm>
#include <cctype>

namespace
{
    std::string lower(std::string s)
    {
        std::transform(s.begin(), s.end(), s.begin(),
                       [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        return s;
    }

    // A whitespace-insensitive, lowercased key for the media NAME-fallback. The backend's game name
    // and ES-DE's scraped gamelist <name> can differ only in spacing around punctuation (e.g.
    // "Pokémon: Let's Go" vs "Pokémon : Let's Go"); dropping whitespace + lowercasing makes them
    // match. Byte-safe for UTF-8 (tolower/isspace on bytes >127 are no-ops in the C locale, so
    // accented characters pass through unchanged).
    std::string normKey(const std::string& s)
    {
        std::string out;
        out.reserve(s.size());
        for (unsigned char c : s)
            if (!std::isspace(c))
                out.push_back(static_cast<char>(std::tolower(c)));
        return out;
    }
} // namespace

GuiMadPagePergameBrowser::GuiMadPagePergameBrowser(
    GuiMadPanel* panel, const std::string& title, const std::string& ns,
    const std::string& system, const std::string& target,
    const std::vector<GuiMadPageStandaloneSections::Section>& menuSections)
    : MadPage {panel, title}
    , mNs {ns}
    , mSystem {system}
    , mTarget {target}
    , mMenuSections {menuSections}
{
}

unsigned int GuiMadPagePergameBrowser::rowColor(const bool overrides)
{
    return overrides ? MadTheme::color(MadColor::Green) : MadTheme::color(MadColor::Primary);
}

void GuiMadPagePergameBrowser::build()
{
    setLoadingText("Loading games…");
    requestGames(/*keepCursor=*/false);
}

void GuiMadPagePergameBrowser::onChildPopped()
{
    // A per-game edit may have created the FIRST override for that game -- re-issue
    // <ns>.games so the "* " badge and the preview summary rebuild with the fresh
    // truth. No spinner: the old list stays visible until the fresh data lands, and
    // the cursor (the game the user just edited) is kept, not reset to the top.
    requestGames(/*keepCursor=*/true);
}

void GuiMadPagePergameBrowser::writeGamesArgs(MadJson::Writer& w)
{
    // The pads target serves the reorder pages, which want the non-lightgun subset.
    if (mTarget == "pads") {
        w.Key("pads");
        w.Bool(true);
    }
}

std::string GuiMadPagePergameBrowser::gameId(const rapidjson::Value& g)
{
    return MadJson::getString(g, "titleid");
}

void GuiMadPagePergameBrowser::requestGames(const bool keepCursor)
{
    pageRequest(
        mNs + ".games", [this](MadJson::Writer& w) { writeGamesArgs(w); },
        [this, keepCursor](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load games: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mGames.clear();
            // The ES-DE system (for media resolution) is fixed per emulator; a
            // subclass that already knows it (RA, per-system) sets mSystem in its
            // ctor, otherwise the backend tells us here (once -- ensureWidgets
            // builds mByStem from it on the first populate()).
            if (mSystem.empty())
                mSystem = MadJson::getString(payload, "system");
            mNote = MadJson::getString(payload, "note"); // empty-state guidance, if any
            parsePayloadExtra(payload);
            const rapidjson::Value& arr {MadJson::getMember(payload, "games")};
            if (arr.IsArray())
                for (const rapidjson::Value& g : arr.GetArray()) {
                    Game game;
                    game.id = gameId(g);
                    game.stem = MadJson::getString(g, "stem");
                    game.name = MadJson::getString(g, "name", game.id);
                    // Accept "override" (new contract) or "overrides" (RA's field name).
                    game.overrides = MadJson::getBool(g, "override", false) ||
                                     MadJson::getBool(g, "overrides", false);
                    game.summary = MadJson::getString(g, "summary");
                    perGameExtra(g, game);
                    mGames.push_back(game);
                }
            populate(keepCursor);
        },
        8000);
}

void GuiMadPagePergameBrowser::ensureWidgets()
{
    if (mList != nullptr)
        return;
    // Reserve the right ~40% of the viewport for the media + overrides-preview
    // text; the list sits in the left ~55% (below a header), a small gap between
    // them -- same proportions as GuiMadPageBezelPerGame, just a media box (art +
    // preview video) stacked above the text.
    const float listWidth {mViewportSize.x * 0.55f};
    const float headerHeight {Font::get(FONT_SIZE_SMALL)->getHeight() * 2.0f};

    mHeader = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                              MadTheme::color(MadColor::Secondary), ALIGN_LEFT,
                                              ALIGN_CENTER, glm::ivec2 {0, 1});
    mHeader->setPosition(mViewportPos.x, mViewportPos.y);
    mHeader->setSize(listWidth, 0.0f); // autosize height (may wrap to two lines)
    addChild(mHeader.get());

    const float listTop {mViewportPos.y + headerHeight};
    mList = std::make_shared<MadVirtualList>();
    mList->setPosition(mViewportPos.x, listTop);
    mList->setSize(listWidth, mViewportPos.y + mViewportSize.y - listTop);
    mList->setOnSelect([this](int i) { openGame(i); });
    mList->setOnCursorChanged([this](int) { updatePreview(); });
    addChild(mList.get());
    mList->onFocusGained(); // the only focusable widget on the page

    const float paneGap {mViewportSize.x * 0.03f};
    const float paneLeft {mViewportPos.x + listWidth + paneGap};
    const float paneWidth {mViewportSize.x - listWidth - paneGap};

    // Media box (art, then preview video after a hover delay) -- top of the pane.
    const float mediaHeight {mViewportSize.y * 0.55f};
    mVideo = std::make_shared<MadVideoComponent>();
    mVideo->setOrigin(0.5f, 0.0f);
    addChild(mVideo.get());
    mVideo->setMaxSize(paneWidth * 0.9f, mediaHeight);
    mVideo->setImageMaxSize(paneWidth * 0.9f, mediaHeight);
    mVideo->setPosition(paneLeft + paneWidth * 0.5f, mViewportPos.y);

    // Overrides-preview text -- below the media box.
    const float textTop {mViewportPos.y + mediaHeight +
                         Font::get(FONT_SIZE_SMALL)->getHeight() * 0.5f};
    mPreview = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                               MadTheme::color(MadColor::Primary), ALIGN_LEFT,
                                               ALIGN_CENTER, glm::ivec2 {0, 1});
    mPreview->setPosition(paneLeft, textTop);
    mPreview->setSize(paneWidth, 0.0f); // autosize height
    addChild(mPreview.get());

    // stem -> FileData*, built ONCE from the live SystemData tree -- media
    // resolution only, so a collection/disabled system (sys == nullptr) just
    // leaves the map empty and the page still works, minus media.
    mByStem.clear();
    mByName.clear();
    SystemData* sys {SystemData::getSystemByName(mSystem)};
    if (sys != nullptr)
        for (FileData* fd : sys->getRootFolder()->getFilesRecursive(GAME)) {
            mByStem[fd->getDisplayName()] = fd;      // ROM filename stem (exact)
            mByName[normKey(fd->getName())] = fd;    // gamelist scraped name (whitespace-insensitive)
        }
}

void GuiMadPagePergameBrowser::populate(const bool keepCursor)
{
    ensureWidgets();

    const std::string f {lower(mFilter)};
    mShown.clear();
    for (const Game& g : mGames)
        if (f.empty() || lower(g.name).find(f) != std::string::npos ||
            lower(g.stem).find(f) != std::string::npos) // match the name OR the rom stem
            mShown.push_back(g);

    if (mGames.empty()) // no games at all -> surface the backend's empty-state guidance
        mHeader->setText(mNote.empty()
                             ? "No games found yet — play a game in this emulator once so it "
                               "appears here."
                             : mNote);
    else
        mHeader->setText(std::to_string(mShown.size()) + (f.empty() ? " games" : " matches") +
                         " · press Y to search");

    // One row per shown game ("* name" = has a per-game override).
    std::vector<MadVirtualList::Row> rows;
    rows.reserve(mShown.size());
    for (const Game& g : mShown)
        rows.push_back({(g.overrides ? "* " : "  ") + g.name, rowColor(g.overrides)});
    mList->setRows(rows, keepCursor);

    mPanel->refreshHelpPrompts();
    updatePreview();
}

void GuiMadPagePergameBrowser::updatePreview()
{
    if (mPreview == nullptr)
        return;
    const int c {mList != nullptr ? mList->cursor() : -1};
    if (c < 0 || c >= static_cast<int>(mShown.size())) {
        mPreview->setText("");
        if (mVideo != nullptr) {
            mVideo->stopVideoPlayer(true);
            mVideo->setImageNoDefault("");
            mVideo->setVideo("");
        }
        return;
    }
    const Game& g {mShown[c]};
    // LOCAL from the preloaded payload -- no per-cursor RPC.
    const std::string subLine {g.sub.empty() ? "" : "\n" + g.sub};
    mPreview->setText(g.name + subLine + previewHeadLines(g) + "\n\n" +
                      (g.summary.empty() ? defaultSummary() : g.summary) + "\n\nA: configure");

    // Media -- resolved straight from ES-DE's own FileData (fallback chain +
    // MediaDirectory both honored), NOT the backend payload.
    if (mVideo != nullptr) {
        FileData* fd {nullptr};
        if (!g.stem.empty()) { // exact: the ROM filename stem (RA + tagged games)
            const auto it {mByStem.find(g.stem)};
            if (it != mByStem.end())
                fd = it->second;
        }
        else { // no stem (e.g. an untagged Switch ROM) -> the gamelist's scraped name. Gated on an
            // EMPTY stem so a stem-carrying game (RA always has one) never falls to the non-unique
            // name index and shows another same-named game's art.
            const auto it {mByName.find(normKey(g.name))};
            if (it != mByName.end())
                fd = it->second;
        }
        mVideo->stopVideoPlayer(true);
        mVideo->setImageNoDefault(fd != nullptr ? fd->getImagePath() : "");
        mVideo->setVideo(fd != nullptr ? fd->getVideoPath() : "");
        mVideo->startVideoPlayer();
    }
}

void GuiMadPagePergameBrowser::openGame(int i)
{
    if (i < 0 || i >= static_cast<int>(mShown.size()))
        return;
    const std::string id {mShown[i].id};
    const std::string name {mShown[i].name};
    const std::string ns {mNs};

    if (mTarget == "pads") {
        if (ns == "lindbergh")
            mPanel->pushPage(new GuiMadPageLindberghPads(mPanel, name + " — Controllers", id));
        else
            mPanel->pushPage(new GuiMadPagePergamePads(mPanel, name + " — Controllers", ns, id));
    }
    else if (mTarget == "settingsmenu") {
        // Per-game sub-menu for THIS game: the server-provided leaves with the picked titleid
        // injected, so each leaf opens its per-game page for this game.
        std::vector<GuiMadPageStandaloneSections::Section> leaves {mMenuSections};
        for (auto& leaf : leaves) {
            leaf.ctxVal = id;
            leaf.title = name + " — " + leaf.label;
        }
        mPanel->pushPage(new GuiMadPageStandaloneSections(mPanel, name + " — Per-game", leaves));
    }
    else if (mTarget == "inputmenu") {
        // Per-game input sub-menu: Controllers (pad -> player) leads, then Mappings.
        std::vector<GuiMadPageStandaloneSections::Section> subs;
        GuiMadPageStandaloneSections::Section ctrl;
        ctrl.label = "Controllers";
        ctrl.sublabel = "which pad is each player";
        ctrl.kind = "pergame_pads";
        ctrl.arg = ns;
        ctrl.title = name + " — Controllers";
        ctrl.ctxVal = id;
        GuiMadPageStandaloneSections::Section maps;
        maps.label = "Mappings";
        maps.sublabel = "USB ports, Player 2, button remaps";
        maps.kind = "pergame_input";
        maps.arg = ns;
        maps.title = name + " — Mappings";
        maps.ctxVal = id;
        subs.push_back(ctrl);
        subs.push_back(maps);
        mPanel->pushPage(new GuiMadPageStandaloneSections(mPanel, name + " — Input", subs));
    }
    else if (mTarget == "input")
        mPanel->pushPage(new GuiMadPageEmuInputMap(mPanel, name + " — Input", ns, "titleid", id));
    else
        mPanel->pushPage(new GuiMadPageEmuSettings(mPanel, name + " — Settings", ns, "titleid", id));
}

void GuiMadPagePergameBrowser::openSearch()
{
    // Stop the preview video before the keyboard covers the page: Window ticks only
    // the top GUI, so the decode thread + audio would otherwise idle/bleed behind it.
    if (mVideo != nullptr)
        mVideo->stopVideoPlayer(true);
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiTextEditKeyboardPopup(
        0.0f, "Search " + mSystem, mFilter,
        [this, alive](const std::string& s) {
            if (alive.expired())
                return;
            mFilter = s;
            populate();
        },
        false, "SEARCH"));
}

bool GuiMadPagePergameBrowser::input(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("y", input) && mList != nullptr) {
        openSearch();
        return true;
    }
    if (onExtraButton(config, input))
        return true;
    return mList != nullptr ? mList->input(config, input) : false;
}

void GuiMadPagePergameBrowser::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

void GuiMadPagePergameBrowser::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->cursor();
    // The FFmpeg decode thread + SDL audio run independently of update() -- a
    // pushed child would otherwise keep decoding/playing behind it.
    if (mVideo != nullptr)
        mVideo->stopVideoPlayer(true);
}

void GuiMadPagePergameBrowser::onRestoreFocus()
{
    if (mList != nullptr)
        mList->setCursor(mFocusCookie);
    updatePreview(); // restart media for the current cursor
}

std::vector<HelpPrompt> GuiMadPagePergameBrowser::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"),
                                     HelpPrompt("a", "configure"), HelpPrompt("y", "search")};
    extraHelpPrompts(prompts);
    if (mList != nullptr && mList->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    prompts.push_back(HelpPrompt("b", "back"));
    return prompts;
}
