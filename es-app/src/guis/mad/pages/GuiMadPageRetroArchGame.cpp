//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRetroArchGame.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageRetroArchGame.h"

#include "FileData.h"
#include "SystemData.h"
#include "Window.h"
#include "guis/GuiTextEditKeyboardPopup.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageStandaloneSections.h"

#include <algorithm>
#include <cctype>
#include <functional>

namespace
{
    std::string lower(std::string s)
    {
        std::transform(s.begin(), s.end(), s.begin(),
                       [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        return s;
    }

    // Column layout matches the backend's own "Settings      default" style
    // (retroarch_game_cmds.py's _settings_line/_input_line/_controllers_line
    // fallbacks) so an all-default game reads identically to a partially-
    // overridden one.
    const char* const kDefaultSummary {"Settings      default\n"
                                       "Input remap   default\n"
                                       "Controllers   default (global)"};
} // namespace

GuiMadPageRetroArchGame::GuiMadPageRetroArchGame(GuiMadPanel* panel, const std::string& system)
    : MadPage {panel, system}
    , mSystem {system}
{
}

unsigned int GuiMadPageRetroArchGame::rowColor(const bool overrides)
{
    return overrides ? MadTheme::color(MadColor::Green) : MadTheme::color(MadColor::Primary);
}

void GuiMadPageRetroArchGame::build()
{
    setLoadingText("Loading games…");
    requestGames(/*keepCursor=*/false);
}

void GuiMadPageRetroArchGame::onChildPopped()
{
    // A per-game Settings / Input remap / Controllers edit may have created
    // the FIRST override for that game — re-issue ragame.games so the "* "
    // badge and the preview panel's summary rebuild with the fresh truth. No
    // spinner: the old list stays visible until the fresh data lands, and the
    // cursor (the game the user just edited) is kept, not reset to the top.
    requestGames(/*keepCursor=*/true);
}

void GuiMadPageRetroArchGame::requestGames(const bool keepCursor)
{
    const std::string system {mSystem};
    pageRequest(
        "ragame.games",
        [system](MadJson::Writer& w) {
            w.Key("system");
            w.String(system.c_str(), static_cast<rapidjson::SizeType>(system.length()));
        },
        [this, keepCursor](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load games: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mGames.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "games")};
            if (arr.IsArray())
                for (const rapidjson::Value& g : arr.GetArray())
                    mGames.push_back({MadJson::getString(g, "stem"),
                                      MadJson::getString(g, "name"),
                                      MadJson::getBool(g, "overrides"),
                                      MadJson::getString(g, "summary")});
            populate(keepCursor);
        },
        8000);
}

void GuiMadPageRetroArchGame::ensureWidgets()
{
    if (mList != nullptr)
        return;
    // Reserve the right ~40% of the viewport for the media + overrides-preview
    // text; the list sits in the left ~55% (below a header), a small gap
    // between them — same proportions as GuiMadPageBezelPerGame, just a
    // media box (art + preview video, ES-DE gamelist parity) stacked above the
    // text instead of a fixed-aspect image pane on its own.
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

    // Media box (art, then preview video after a hover delay) — top of the
    // pane, same centered/top-anchored placement GuiMadPageBezelPerGame uses
    // for its bezel ImageComponent.
    const float mediaHeight {mViewportSize.y * 0.55f};
    mVideo = std::make_shared<MadVideoComponent>();
    mVideo->setOrigin(0.5f, 0.0f);
    addChild(mVideo.get());
    mVideo->setMaxSize(paneWidth * 0.9f, mediaHeight);
    mVideo->setImageMaxSize(paneWidth * 0.9f, mediaHeight);
    mVideo->setPosition(paneLeft + paneWidth * 0.5f, mViewportPos.y);

    // Overrides-preview text — below the media box.
    const float textTop {mViewportPos.y + mediaHeight +
                         Font::get(FONT_SIZE_SMALL)->getHeight() * 0.5f};
    mPreview = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                               MadTheme::color(MadColor::Primary), ALIGN_LEFT,
                                               ALIGN_CENTER, glm::ivec2 {0, 1});
    mPreview->setPosition(paneLeft, textTop);
    mPreview->setSize(paneWidth, 0.0f); // autosize height
    addChild(mPreview.get());

    // stem -> FileData*, built ONCE from the live SystemData tree — media
    // resolution only, so a collection/disabled system (sys == nullptr) just
    // leaves the map empty and the page still works, minus media.
    mByStem.clear();
    SystemData* sys {SystemData::getSystemByName(mSystem)};
    if (sys != nullptr)
        for (FileData* fd : sys->getRootFolder()->getFilesRecursive(GAME))
            mByStem[fd->getDisplayName()] = fd;
}

void GuiMadPageRetroArchGame::populate(const bool keepCursor)
{
    ensureWidgets();

    const std::string f {lower(mFilter)};
    mShown.clear();
    for (const Game& g : mGames)
        if (f.empty() || lower(g.name).find(f) != std::string::npos ||
            lower(g.stem).find(f) != std::string::npos) // match the name OR the rom stem
            mShown.push_back(g);

    mHeader->setText(std::to_string(mShown.size()) + (f.empty() ? " games" : " matches") +
                     " · press Y to search");

    // One row per shown game ("* name" = has a per-game override). The list
    // builds only the on-screen rows — no cap even at ~11k games.
    std::vector<MadVirtualList::Row> rows;
    rows.reserve(mShown.size());
    for (const Game& g : mShown)
        rows.push_back({(g.overrides ? "* " : "  ") + g.name, rowColor(g.overrides)});
    mList->setRows(rows, keepCursor);

    mPanel->refreshHelpPrompts();
    updatePreview();
}

void GuiMadPageRetroArchGame::updatePreview()
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
    // LOCAL from the preloaded payload — no per-cursor RPC.
    mPreview->setText(g.name + "\n\n" + (g.summary.empty() ? kDefaultSummary : g.summary) +
                      "\n\nA: configure");

    // Media — resolved straight from ES-DE's own FileData (fallback chain +
    // MediaDirectory both honored by getImagePath()/getVideoPath()), NOT the
    // backend payload. stopVideoPlayer(true) mutes/joins the outgoing video
    // immediately (same as GamelistView's per-cursor switch); startVideoPlayer()
    // re-arms the art-then-video pre-roll — a rapid scroll just keeps
    // re-arming it, so FFmpeg only spins up once the user actually pauses.
    if (mVideo != nullptr) {
        const auto it {mByStem.find(g.stem)};
        FileData* fd {it != mByStem.end() ? it->second : nullptr};
        mVideo->stopVideoPlayer(true);
        mVideo->setImageNoDefault(fd != nullptr ? fd->getImagePath() : "");
        mVideo->setVideo(fd != nullptr ? fd->getVideoPath() : "");
        mVideo->startVideoPlayer();
    }
}

void GuiMadPageRetroArchGame::openGame(int i)
{
    if (i < 0 || i >= static_cast<int>(mShown.size()))
        return;
    const std::string tid {mSystem + ":" + mShown[i].stem};
    const std::string name {mShown[i].name};

    // Same in-memory-Section construction as GuiMadPageGamePicker's
    // "inputmenu" case (GuiMadPageGamePicker.cpp:115-137): build the
    // per-game chooser's rows here instead of round-tripping the backend.
    std::vector<GuiMadPageStandaloneSections::Section> subs;

    GuiMadPageStandaloneSections::Section settings;
    settings.label = "Settings";
    settings.kind = "pergame_settings";
    settings.arg = "ragameset";
    settings.title = name + " — Settings";
    settings.ctxVal = tid;
    subs.push_back(settings);

    GuiMadPageStandaloneSections::Section remap;
    remap.label = "Input remap";
    remap.kind = "pergame_settings";
    remap.arg = "ragamein";
    remap.title = name + " — Input remap";
    remap.ctxVal = tid;
    subs.push_back(remap);

    GuiMadPageStandaloneSections::Section controllers;
    controllers.label = "Controllers";
    controllers.kind = "pergame_priority";
    controllers.title = name + " — Controllers";
    controllers.ctxVal = tid;
    subs.push_back(controllers);

    mPanel->pushPage(new GuiMadPageStandaloneSections(mPanel, name, subs));
}

void GuiMadPageRetroArchGame::openSearch()
{
    // Stop the preview video before the keyboard covers the page: Window ticks
    // only the top GUI, so the decode thread + audio would otherwise idle/bleed
    // behind it. populate() -> updatePreview() re-arms it on submit or cancel.
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

bool GuiMadPageRetroArchGame::input(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("y", input) && mList != nullptr) {
        openSearch();
        return true;
    }
    return mList != nullptr ? mList->input(config, input) : false;
}

void GuiMadPageRetroArchGame::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

void GuiMadPageRetroArchGame::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->cursor();
    // The FFmpeg decode thread + SDL audio run independently of update() — a
    // pushed child chooser (openGame()) would otherwise keep decoding/playing
    // behind it and bleed audio into that page.
    if (mVideo != nullptr)
        mVideo->stopVideoPlayer(true);
}

void GuiMadPageRetroArchGame::onRestoreFocus()
{
    if (mList != nullptr)
        mList->setCursor(mFocusCookie);
    updatePreview(); // restart media for the current cursor
}

std::vector<HelpPrompt> GuiMadPageRetroArchGame::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"),
                                     HelpPrompt("a", "configure"), HelpPrompt("y", "search")};
    if (mList != nullptr && mList->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    prompts.push_back(HelpPrompt("b", "back"));
    return prompts;
}
