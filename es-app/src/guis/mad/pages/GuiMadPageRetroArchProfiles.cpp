//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRetroArchProfiles.cpp
//
//  MAD control panel: RetroArch input PROFILES root list (deck-patches, P3).
//

#include "guis/mad/pages/GuiMadPageRetroArchProfiles.h"

#include "Sound.h" // NavigationSounds / SCROLLSOUND (LT/RT page scroll)
#include "Window.h" // mWindow->pushGui needs the complete type
#include "guis/GuiTextEditKeyboardPopup.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageEmuSettings.h"
#include "utils/StringUtil.h"

#include <algorithm>

GuiMadPageRetroArchProfiles::GuiMadPageRetroArchProfiles(GuiMadPanel* panel,
                                                         const std::string& title)
    : MadPage {panel, title}
{
}

void GuiMadPageRetroArchProfiles::build()
{
    setLoadingText("Loading profiles...");
    pageRequest("raprof.list", nullptr, [this](bool ok, const rapidjson::Value& payload) {
        setLoadingText("");
        if (!ok) {
            footer()->setStatus("Couldn't list profiles: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                true);
            return;
        }
        rebuild(payload);
    });
}

void GuiMadPageRetroArchProfiles::onChildPopped()
{
    // A create / delete / reset, or an edit that moved a family assignment, may have changed the
    // list -- re-fetch from truth.
    build();
}

void GuiMadPageRetroArchProfiles::rebuild(const rapidjson::Value& result)
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
    if (mScroll != nullptr) {
        mScrollCookie = mScroll->scrollOffset();
        removeChild(mScroll.get());
        mScroll.reset();
    }
    mIntro.reset();
    mGrid.reset();

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};

    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};
    mIntro = std::make_shared<TextComponent>(
        "Named hotkey profiles. Assign each to controller families; at launch the router applies "
        "the profile of whichever pad it seats on player 1. Pick one to edit, or make a new one.",
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(0.0f, y);
    mIntro->setSize(mViewportSize.x, 0.0f);
    mScroll->addChild(mIntro.get());
    y += mIntro->getSize().y + smallHeight * 0.4f;

    std::vector<MadTileGrid::Tile> tiles;

    // The FIRST tile creates a new profile. Its sentinel key is the empty string: a real profile
    // name is never blank (the backend rejects blank / control-char names), so onPick can tell them
    // apart without a magic string that a user could accidentally choose.
    MadTileGrid::Tile add;
    add.key = "";
    add.label = "+ New profile";
    add.sublabel = "Create a profile";
    tiles.emplace_back(add);

    const rapidjson::Value& profiles {MadJson::getMember(result, "profiles")};
    if (profiles.IsArray()) {
        for (rapidjson::SizeType i {0}; i < profiles.Size(); ++i) {
            const rapidjson::Value& row {profiles[i]};
            MadTileGrid::Tile tile;
            tile.key = MadJson::getString(row, "name");
            tile.label = tile.key;
            const bool shipped {MadJson::getBool(row, "shipped")};
            const bool shadowed {MadJson::getBool(row, "shadowed")};
            if (!shipped) {
                tile.sublabel = "Custom";
                tile.badge = true; // locally-owned
            }
            else if (shadowed) {
                tile.sublabel = "Shipped (edited)";
                tile.badge = true; // has a local override
            }
            else {
                tile.sublabel = "Shipped";
            }
            tiles.emplace_back(tile);
        }
    }

    mGrid = std::make_shared<MadTileGrid>();
    mGrid->setPosition(0.0f, y);
    mGrid->setSize(mViewportSize.x, 1.0f);
    mGrid->setTiles(tiles);
    mGrid->setSize(mViewportSize.x, std::max(1.0f, mGrid->contentHeight()));
    mGrid->setOnPick([this](const std::string& key) {
        if (key.empty())
            promptCreate();
        else
            openProfile(key);
    });
    mGrid->setCursorIndex(mGridCookie);
    mGrid->onFocusGained(); // the grid is this page's only focusable
    mScroll->addChild(mGrid.get());
    y += mGrid->getSize().y;

    // Load-bearing (a review finding): without setContentHeight the MadScrollView never overflows,
    // so a grid taller than the viewport is clipped and its rows become unreachable. Mirrors
    // GuiMadPageBackends -- give the scroll its real content height, then follow the cursor.
    mScroll->setContentHeight(y + smallHeight * 0.5f);
    mScroll->setScrollOffset(mScrollCookie);
    followFocus();
    mPanel->refreshHelpPrompts();
}

void GuiMadPageRetroArchProfiles::followFocus()
{
    if (mScroll == nullptr || mGrid == nullptr)
        return;
    const glm::vec2 row {mGrid->cursorRowRect()};
    // Top row: reveal the intro above it too.
    if (mGrid->cursorIndex() / std::max(1, mGrid->columns()) == 0)
        mScroll->ensureVisible(0.0f, mGrid->getPosition().y + row.y);
    else
        mScroll->ensureVisible(mGrid->getPosition().y + row.x, mGrid->getPosition().y + row.y);
}

bool GuiMadPageRetroArchProfiles::input(InputConfig* config, Input input)
{
    if (mGrid == nullptr)
        return false;
    if (mGrid->input(config, input)) {
        followFocus();
        return true;
    }
    return false;
}

void GuiMadPageRetroArchProfiles::pageScroll(int direction)
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

std::vector<HelpPrompt> GuiMadPageRetroArchProfiles::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (mGrid != nullptr)
        prompts = mGrid->getHelpPrompts();
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void GuiMadPageRetroArchProfiles::onSaveFocus()
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void GuiMadPageRetroArchProfiles::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mGridCookie);
    if (mScroll != nullptr)
        mScroll->setScrollOffset(mScrollCookie);
    followFocus();
}

void GuiMadPageRetroArchProfiles::openProfile(const std::string& name)
{
    // The per-profile editor is the generic buffered settings page, targeting THIS profile via
    // ctxKey/ctxVal ("profile", <name>).
    mPanel->pushPage(new GuiMadPageEmuSettings(mPanel, name, "raprof", "profile", name));
}

void GuiMadPageRetroArchProfiles::promptCreate()
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiTextEditKeyboardPopup(
        0.0f, "New profile name", "",
        [this, alive](const std::string& text) {
            if (alive.expired())
                return;
            const std::string name {Utils::String::trim(text)};
            if (name.empty())
                return;
            pageRequest(
                "raprof.create",
                [name](MadJson::Writer& w) {
                    w.Key("name");
                    w.String(name.c_str(), static_cast<rapidjson::SizeType>(name.length()));
                },
                [this](bool ok, const rapidjson::Value& payload) {
                    if (!ok) {
                        footer()->flash("Couldn't create: " +
                                            MadJson::getString(payload, "message", "unknown error"),
                                        4000, true);
                        return;
                    }
                    const std::string created {MadJson::getString(payload, "created", "")};
                    build(); // refresh the grid with the new profile
                    if (!created.empty())
                        openProfile(created); // and jump straight into it
                });
        },
        false, "CREATE"));
}
