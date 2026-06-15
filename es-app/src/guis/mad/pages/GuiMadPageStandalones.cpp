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

GuiMadPageStandalones::GuiMadPageStandalones(GuiMadPanel* panel, const std::string& title,
                                             const std::string& membersJson)
    : MadPage {panel, title}
    , mIsSub {true}
    , mProvidedJson {membersJson}
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
    pageRequest("standalones.list", nullptr, [this](bool ok, const rapidjson::Value& payload) {
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
        const auto lit = mLabelByKey.find(key);
        const std::string title {lit != mLabelByKey.end() ? lit->second : key};
        mPanel->pushPage(new GuiMadPageStandalones(mPanel, title, git->second));
        return;
    }
    const auto it = mSectionsByKey.find(key);
    if (it == mSectionsByKey.end() || it->second.empty())
        return;
    const std::vector<GuiMadPageStandaloneSections::Section>& secs {it->second};
    // One section opens its page directly; several show a small chooser.
    if (secs.size() == 1) {
        const GuiMadPageStandaloneSections::Section& s {secs.front()};
        madOpenStandaloneTarget(mPanel, s.kind, s.arg, s.title);
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
    mGroupJsonByKey.clear();

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};

    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};

    mIntro = std::make_shared<TextComponent>(
        mIsSub ? "Pick an emulator to configure — different games can run better on different "
                 "ones."
               : "Every standalone emulator in one place — pick one to configure its settings and "
                 "controllers without leaving ES-DE.",
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(0.0f, y);
    mIntro->setSize(mViewportSize.x, 0.0f);
    mScroll->addChild(mIntro.get());
    y += mIntro->getSize().y + smallHeight * 0.4f;

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
                continue;
            }

            std::vector<GuiMadPageStandaloneSections::Section> secs;
            const rapidjson::Value& sa {MadJson::getMember(row, "sections")};
            if (sa.IsArray()) {
                for (rapidjson::SizeType j {0}; j < sa.Size(); ++j) {
                    const rapidjson::Value& sv {sa[j]};
                    secs.push_back({MadJson::getString(sv, "label"),
                                    MadJson::getString(sv, "sublabel"),
                                    MadJson::getString(sv, "kind"),
                                    MadJson::getString(sv, "arg"),
                                    MadJson::getString(sv, "title")});
                }
            }
            mSectionsByKey[tile.key] = secs;
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
