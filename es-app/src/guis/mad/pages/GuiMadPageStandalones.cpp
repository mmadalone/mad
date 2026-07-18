//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageStandalones.cpp
//
//  MAD control panel: Standalones hub (deck-patches). Mirrors the GuiMadPageBackends
//  tile-grid pattern; on pick it pushes the chosen emulator's existing config page.
//

#include "guis/mad/pages/GuiMadPageStandalones.h"

#include "Sound.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPagePergameBrowser.h"      // settings_pergame_menu collapse target
#include "guis/mad/pages/GuiMadPageStandaloneSections.h" // madOpenStandaloneTarget + sub-chooser

#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>

#include <cmath>

GuiMadPageStandalones::GuiMadPageStandalones(GuiMadPanel* panel)
    : MadPage {panel, "STANDALONES"}
    , mGridCookie {0}
    , mScrollCookie {0.0f}
{
}

GuiMadPageStandalones::GuiMadPageStandalones(GuiMadPanel* panel, Fetch,
                                             const std::string& listMethod,
                                             const std::string& title)
    : MadPage {panel, title}
    , mListMethod {listMethod}
    , mGridCookie {0}
    , mScrollCookie {0.0f}
{
}

GuiMadPageStandalones::GuiMadPageStandalones(GuiMadPanel* panel, const std::string& title,
                                             const std::string& membersJson,
                                             const std::string& intro)
    : MadPage {panel, title}
    , mIsSub {true}
    , mProvidedJson {membersJson}
    , mSubIntro {intro}
    , mGridCookie {0}
    , mScrollCookie {0.0f}
{
}

void GuiMadPageStandalones::build()
{
    if (mIsSub) {
        // Sub-grid page: render the provided members payload (no fetch).
        rapidjson::Document doc;
        doc.Parse(mProvidedJson.c_str());
        rebuild(doc);
        return;
    }
    setLoadingText("Loading standalone emulators…");
    pageRequest(mListMethod, nullptr, [this](bool ok, const rapidjson::Value& payload) {
        setLoadingText("");
        if (!ok) {
            footer()->setStatus("Couldn't list standalone emulators: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                true);
            return;
        }
        rebuild(payload);
    });
}

void GuiMadPageStandalones::onChildPopped()
{
    build();
}

void GuiMadPageStandalones::open(const std::string& key)
{
    // GROUP tile → push a sub-grid of its members (reuses this page's tile grid).
    const auto git = mGroupJsonByKey.find(key);
    if (git != mGroupJsonByKey.end()) {
        const auto tit = mTitleByKey.find(key);
        const std::string title {tit != mTitleByKey.end() ? tit->second : key};
        // Inherit this grid's intro so a per-game group sub-grid (System/Video of a picked game)
        // keeps the game-context line instead of falling back to the emulator-picker default. The
        // root standalones grid has an empty mSubIntro, so Switch/pcsx2x6 sub-grids are unchanged.
        mPanel->pushPage(new GuiMadPageStandalones(mPanel, title, git->second, mSubIntro));
        return;
    }
    const auto it = mSectionsByKey.find(key);
    if (it == mSectionsByKey.end() || it->second.empty())
        return;
    const std::vector<GuiMadPageStandaloneSections::Section>& secs {it->second};
    // One section opens its page directly; several show a small chooser. A lone
    // "toggle" is the exception: it is an INLINE chip, not a page to open, so it
    // falls through to the chooser (which renders the chip in place) -- e.g. MUGEN,
    // whose only section is its X-Arcade warning toggle.
    if (secs.size() == 1 && secs.front().kind != "toggle") {
        const GuiMadPageStandaloneSections::Section& s {secs.front()};
        // Two kinds are handled by GuiMadPageStandaloneSections' row dispatcher but NOT by the
        // free madOpenStandaloneTarget (they carry payload the free function never receives), so a
        // LONE tile of either kind would open nothing -- mirror the row handlers here:
        //   settings_pergame_menu: needs the game-first sub-menu leaves (s.subsections) -- e.g. a
        //     gridified Per-game member or Lindbergh's single-section tile.
        //   grid: needs the sub-grid payload (s.tilesJson/s.note) -- e.g. the On-the-go Per-system
        //     tile, whose only section is a grid of per-system handheld editors.
        if (s.kind == "settings_pergame_menu")
            mPanel->pushPage(new GuiMadPagePergameBrowser(mPanel, s.title, s.arg, "",
                                                          "settingsmenu", s.subsections));
        else if (s.kind == "grid")
            mPanel->pushPage(new GuiMadPageStandalones(mPanel, s.title, s.tilesJson, s.note));
        else
            // openLeaf handles the per-game kinds (they carry the picked titleid in ctxVal, so a
            // tiled per-game menu's leaf tiles dispatch correctly) and falls back to the free opener.
            GuiMadPageStandaloneSections::openLeaf(mPanel, s);
    }
    else {
        const auto lit = mLabelByKey.find(key);
        const std::string title {lit != mLabelByKey.end() ? lit->second : key};
        mPanel->pushPage(new GuiMadPageStandaloneSections(mPanel, title, secs));
    }
}

void GuiMadPageStandalones::rebuild(const rapidjson::Value& result)
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
    mIntro.reset();
    mGrid.reset();
    if (mScroll != nullptr) {
        removeChild(mScroll.get());
        mScroll.reset();
    }
    mSectionsByKey.clear();
    mLabelByKey.clear();
    mTitleByKey.clear();
    mGroupJsonByKey.clear();

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};

    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};

    // A sub-grid shows a caption only when it carries an explicit mSubIntro (the genuine
    // multi-emulator picker sets one). The config sub-grids (GameCube/Wii input, per-game, ...)
    // carry none, so they show NO caption -- the old empty-mSubIntro fallback wrongly leaked
    // "Pick an emulator to configure" onto every one of them.
    const std::string introText {
        mIsSub ? mSubIntro
               : "Every standalone emulator in one place — pick one to configure its settings and "
                 "controllers without leaving ES-DE."};
    if (!introText.empty()) {
        mIntro = std::make_shared<TextComponent>(
            introText, Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary),
            ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 1});
        mIntro->setPosition(0.0f, y);
        mIntro->setSize(mViewportSize.x, 0.0f);
        mScroll->addChild(mIntro.get());
        y += mIntro->getSize().y + smallHeight * 0.4f;
    }

    std::vector<MadTileGrid::Tile> tiles;
    const rapidjson::Value& arr {MadJson::getMember(result, "tiles")};
    if (arr.IsArray()) {
        for (rapidjson::SizeType i {0}; i < arr.Size(); ++i) {
            const rapidjson::Value& row {arr[i]};
            MadTileGrid::Tile tile;
            tile.key = MadJson::getString(row, "key");
            tile.label = MadJson::getString(row, "label", tile.key);
            tile.sublabel = MadJson::getString(row, "sublabel");
            const rapidjson::Value& art {MadJson::getMember(row, "art")};
            if (art.IsArray() && art.Size() > 0 && art[0].IsString())
                tile.artPath = art[0].GetString();
            tiles.emplace_back(tile);

            // GROUP tile (e.g. Switch): serialize its members into a
            // {"tiles":[…]} payload that a sub-grid page can render directly.
            // open(key) routes group keys to a sub-grid before the sections flow.
            const rapidjson::Value& mem {MadJson::getMember(row, "members")};
            if (mem.IsArray()) {
                rapidjson::StringBuffer buf;
                rapidjson::Writer<rapidjson::StringBuffer> writer {buf};
                writer.StartObject();
                writer.Key("tiles");
                mem.Accept(writer);
                writer.EndObject();
                mGroupJsonByKey[tile.key] = buf.GetString();
                mLabelByKey[tile.key] = tile.label;
                // A per-game group tile carries a game-qualified "title" ("<game> - System"); the
                // sub-grid header uses it, else the bare label (Switch etc. have no "title").
                mTitleByKey[tile.key] = MadJson::getString(row, "title", tile.label);
                continue;
            }

            mSectionsByKey[tile.key] =
                GuiMadPageStandaloneSections::parseSections(MadJson::getMember(row, "sections"));
            mLabelByKey[tile.key] = tile.label;
        }
    }

    if (tiles.empty()) {
        setLoadingText("No standalone emulators found in ES-DE.");
    }
    else {
        mGrid = std::make_shared<MadTileGrid>();
        mGrid->setPosition(0.0f, y);
        mGrid->setSize(mViewportSize.x, 1.0f);
        mGrid->setTiles(tiles);
        mGrid->setSize(mViewportSize.x, std::max(1.0f, mGrid->contentHeight()));
        mGrid->setOnPick([this](const std::string& key) { open(key); });
        mGrid->setCursorIndex(mGridCookie);
        mGrid->onFocusGained();
        mScroll->addChild(mGrid.get());
        y += mGrid->getSize().y;
    }

    mScroll->setContentHeight(y + smallHeight * 0.5f);
    mScroll->setScrollOffset(mScrollCookie);
    followFocus();
    mPanel->refreshHelpPrompts();
}

void GuiMadPageStandalones::followFocus()
{
    if (mScroll == nullptr || mGrid == nullptr)
        return;
    const glm::vec2 row {mGrid->cursorRowRect()};
    if (mGrid->cursorIndex() / std::max(1, mGrid->columns()) == 0)
        mScroll->ensureVisible(0.0f, mGrid->getPosition().y + row.y);
    else
        mScroll->ensureVisible(mGrid->getPosition().y + row.x, mGrid->getPosition().y + row.y);
}

bool GuiMadPageStandalones::input(InputConfig* config, Input input)
{
    if (mGrid == nullptr)
        return false;
    if (mGrid->input(config, input)) {
        followFocus();
        return true;
    }
    return false;
}

void GuiMadPageStandalones::pageScroll(int direction)
{
    if (mScroll == nullptr || mGrid == nullptr)
        return;
    if (!mScroll->overflows()) {
        mGrid->pageScroll(direction);
        followFocus();
        return;
    }
    std::vector<PagedTarget> targets;
    for (int row {0}; row < mGrid->rows(); ++row) {
        const glm::vec2 rect {mGrid->rowRect(row)};
        targets.push_back({0, row, mGrid->getPosition().y + rect.x,
                           mGrid->getPosition().y + rect.y});
    }
    bool moved {mScroll->pageScroll(direction)};
    const float viewTop {mScroll->scrollOffset()};
    const int pick {
        pickPagedTarget(targets, direction, viewTop, viewTop + mScroll->getSize().y)};
    if (pick >= 0) {
        const int columns {std::max(1, mGrid->columns())};
        const int column {mGrid->cursorIndex() % columns};
        const int target {
            std::min(targets[pick].aux * columns + column, mGrid->tileCount() - 1)};
        if (target != mGrid->cursorIndex()) {
            mGrid->setCursorIndex(target);
            moved = true;
        }
        followFocus();
    }
    if (moved)
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
}

std::vector<HelpPrompt> GuiMadPageStandalones::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (mGrid != nullptr)
        prompts = mGrid->getHelpPrompts();
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void GuiMadPageStandalones::onSaveFocus()
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void GuiMadPageStandalones::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mGridCookie);
    if (mScroll != nullptr)
        mScroll->setScrollOffset(mScrollCookie);
    followFocus();
}
