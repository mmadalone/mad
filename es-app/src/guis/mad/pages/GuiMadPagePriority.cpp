//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePriority.cpp
//
//  MAD control panel: Priority section (deck-patches).
//

#include "guis/mad/pages/GuiMadPagePriority.h"

#include "Sound.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "utils/StringUtil.h"

#include <cmath>
#include "guis/mad/MadTheme.h"

namespace
{
    std::vector<MadTileGrid::Tile> tilesFromArray(const rapidjson::Value& rows,
                                                  const bool collections)
    {
        std::vector<MadTileGrid::Tile> tiles;
        if (!rows.IsArray())
            return tiles;
        for (rapidjson::SizeType i {0}; i < rows.Size(); ++i) {
            const rapidjson::Value& row {rows[i]};
            MadTileGrid::Tile tile;
            tile.key = MadJson::getString(row, "name");
            tile.label = tile.key;
            tile.sublabel = "P1: " + MadJson::getString(row, "p1", "(empty)");
            if (collections && MadJson::getBool(row, "lightgun"))
                tile.sublabel += "  [lightgun]";
            tile.artPath = MadJson::getString(row, "art");
            tiles.emplace_back(tile);
        }
        return tiles;
    }
} // namespace

//  ── GuiMadPagePriority (root) ──

GuiMadPagePriority::GuiMadPagePriority(GuiMadPanel* panel)
    : MadPage {panel, "CONTROLLER PRIORITY"}
    , mFocusTarget {FocusAddSystem}
    , mSystemGridCookie {0}
    , mCollectionGridCookie {0}
    , mScrollCookie {0.0f}
    , mBuilt {false}
{
}

void GuiMadPagePriority::build()
{
    setLoadingText("Loading priority rules…");
    pageRequest(
        "priority.list", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load the priority rules: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            rebuild(payload);
        },
        10000);
}

void GuiMadPagePriority::onChildPopped()
{
    build(); // A picker/editor may have added, changed, or cleared a rule.
}

void GuiMadPagePriority::rebuild(const rapidjson::Value& result)
{
    if (mSystemGrid != nullptr)
        mSystemGridCookie = mSystemGrid->cursorIndex();
    if (mCollectionGrid != nullptr)
        mCollectionGridCookie = mCollectionGrid->cursorIndex();
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
    // Children first (dtors self-detach), then the scroll view.
    mIntro.reset();
    mSystemsHeader.reset();
    mAddSystem.reset();
    mNoSystems.reset();
    mSystemGrid.reset();
    mCollectionsHeader.reset();
    mAddCollection.reset();
    mNoCollections.reset();
    mCollectionGrid.reset();
    if (mScroll != nullptr) {
        removeChild(mScroll.get());
        mScroll.reset();
    }

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};

    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};

    mIntro = std::make_shared<TextComponent>(
        "Preferred controller per system (top = Player 1). RetroArch systems only — "
        "standalone emulators are configured on the Backends page. A custom COLLECTION rule "
        "overrides the system rule for its member games (e.g. a lightgun collection).",
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(0.0f, y);
    mIntro->setSize(mViewportSize.x, 0.0f);
    mScroll->addChild(mIntro.get());
    y += mIntro->getSize().y + smallHeight * 0.4f;

    mSystemsHeader = std::make_shared<TextComponent>(
        "Configured systems", Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Title), ALIGN_LEFT,
        ALIGN_CENTER, glm::ivec2 {0, 0});
    mSystemsHeader->setPosition(0.0f, y);
    mSystemsHeader->setSize(mViewportSize.x, smallHeight);
    mScroll->addChild(mSystemsHeader.get());
    y += smallHeight + smallHeight * 0.15f;

    mAddSystem = std::make_shared<ButtonComponent>(
        "CONFIGURE A SYSTEM", "configure a system",
        [this] { mPanel->pushPage(new GuiMadPagePriorityPicker(mPanel, "system")); });
    mAddSystem->setPosition(0.0f, y);
    mScroll->addChild(mAddSystem.get());
    y += mAddSystem->getSize().y + smallHeight * 0.3f;

    const std::vector<MadTileGrid::Tile> systemTiles {
        tilesFromArray(MadJson::getMember(result, "systems"), false)};
    if (systemTiles.empty()) {
        mNoSystems = std::make_shared<TextComponent>(
            "  (none configured yet)", Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary),
            ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
        mNoSystems->setPosition(0.0f, y);
        mNoSystems->setSize(mViewportSize.x, smallHeight);
        mScroll->addChild(mNoSystems.get());
        y += smallHeight;
    }
    else {
        mSystemGrid = std::make_shared<MadTileGrid>();
        mSystemGrid->setPosition(0.0f, y);
        mSystemGrid->setSize(mViewportSize.x, 1.0f);
        mSystemGrid->setTiles(systemTiles);
        mSystemGrid->setSize(mViewportSize.x, std::max(1.0f, mSystemGrid->contentHeight()));
        mSystemGrid->setOnPick([this](const std::string& name) {
            mPanel->pushPage(new GuiMadPagePriorityEdit(mPanel, name, "system"));
        });
        mSystemGrid->setCursorIndex(mSystemGridCookie);
        mScroll->addChild(mSystemGrid.get());
        y += mSystemGrid->getSize().y;
    }
    y += smallHeight * 0.6f;

    mCollectionsHeader = std::make_shared<TextComponent>(
        "Configured collections", Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Title), ALIGN_LEFT,
        ALIGN_CENTER, glm::ivec2 {0, 0});
    mCollectionsHeader->setPosition(0.0f, y);
    mCollectionsHeader->setSize(mViewportSize.x, smallHeight);
    mScroll->addChild(mCollectionsHeader.get());
    y += smallHeight + smallHeight * 0.15f;

    mAddCollection = std::make_shared<ButtonComponent>(
        "CONFIGURE A COLLECTION", "configure a collection",
        [this] { mPanel->pushPage(new GuiMadPagePriorityPicker(mPanel, "collection")); });
    mAddCollection->setPosition(0.0f, y);
    mScroll->addChild(mAddCollection.get());
    y += mAddCollection->getSize().y + smallHeight * 0.3f;

    const std::vector<MadTileGrid::Tile> collectionTiles {
        tilesFromArray(MadJson::getMember(result, "collections"), true)};
    if (collectionTiles.empty()) {
        mNoCollections = std::make_shared<TextComponent>(
            "  (none configured yet)", Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary),
            ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
        mNoCollections->setPosition(0.0f, y);
        mNoCollections->setSize(mViewportSize.x, smallHeight);
        mScroll->addChild(mNoCollections.get());
        y += smallHeight;
    }
    else {
        mCollectionGrid = std::make_shared<MadTileGrid>();
        mCollectionGrid->setPosition(0.0f, y);
        mCollectionGrid->setSize(mViewportSize.x, 1.0f);
        mCollectionGrid->setTiles(collectionTiles);
        mCollectionGrid->setSize(mViewportSize.x,
                                 std::max(1.0f, mCollectionGrid->contentHeight()));
        mCollectionGrid->setOnPick([this](const std::string& name) {
            mPanel->pushPage(new GuiMadPagePriorityEdit(mPanel, name, "collection"));
        });
        mCollectionGrid->setCursorIndex(mCollectionGridCookie);
        mScroll->addChild(mCollectionGrid.get());
        y += mCollectionGrid->getSize().y;
    }

    mScroll->setContentHeight(y + smallHeight * 0.5f);
    mScroll->setScrollOffset(mScrollCookie);

    mBuilt = true;
    // The cookied focus target may no longer exist (e.g. last rule cleared).
    if ((mFocusTarget == FocusSystemGrid && mSystemGrid == nullptr) ||
        (mFocusTarget == FocusCollectionGrid && mCollectionGrid == nullptr))
        mFocusTarget = FocusAddSystem;
    setFocusTarget(mFocusTarget);
    followFocus();
}

int GuiMadPagePriority::nextTarget(int target, const int direction) const
{
    while (true) {
        target += direction;
        if (target < FocusAddSystem || target > FocusCollectionGrid)
            return -1;
        if (target == FocusSystemGrid && mSystemGrid == nullptr)
            continue;
        if (target == FocusCollectionGrid && mCollectionGrid == nullptr)
            continue;
        return target;
    }
}

void GuiMadPagePriority::setFocusTarget(const int target)
{
    mFocusTarget = target;
    auto applyButton = [target](const std::shared_ptr<ButtonComponent>& button,
                                const int focusId) {
        if (button == nullptr)
            return;
        if (target == focusId)
            button->onFocusGained();
        else
            button->onFocusLost();
    };
    applyButton(mAddSystem, FocusAddSystem);
    applyButton(mAddCollection, FocusAddCollection);
    if (mSystemGrid != nullptr) {
        if (target == FocusSystemGrid)
            mSystemGrid->onFocusGained();
        else
            mSystemGrid->onFocusLost();
    }
    if (mCollectionGrid != nullptr) {
        if (target == FocusCollectionGrid)
            mCollectionGrid->onFocusGained();
        else
            mCollectionGrid->onFocusLost();
    }
    mPanel->refreshHelpPrompts();
}

void GuiMadPagePriority::moveFocus(const int target)
{
    setFocusTarget(target);
    followFocus();
}

void GuiMadPagePriority::followFocus()
{
    if (mScroll == nullptr)
        return;
    float top {0.0f};
    float bottom {0.0f};
    switch (mFocusTarget) {
        case FocusAddSystem: {
            // Topmost focusable: reveal the intro above it too.
            top = 0.0f;
            bottom = mAddSystem->getPosition().y + mAddSystem->getSize().y;
            break;
        }
        case FocusSystemGrid: {
            if (mSystemGrid == nullptr)
                return;
            const glm::vec2 row {mSystemGrid->cursorRowRect()};
            top = mSystemGrid->getPosition().y + row.x;
            bottom = mSystemGrid->getPosition().y + row.y;
            break;
        }
        case FocusAddCollection: {
            // Reveal the collections header above the button.
            top = mCollectionsHeader->getPosition().y;
            bottom = mAddCollection->getPosition().y + mAddCollection->getSize().y;
            break;
        }
        case FocusCollectionGrid: {
            if (mCollectionGrid == nullptr)
                return;
            const glm::vec2 row {mCollectionGrid->cursorRowRect()};
            top = mCollectionGrid->getPosition().y + row.x;
            bottom = mCollectionGrid->getPosition().y + row.y;
            break;
        }
        default:
            return;
    }
    mScroll->ensureVisible(top, bottom);
}

bool GuiMadPagePriority::input(InputConfig* config, Input input)
{
    if (!mBuilt)
        return false;

    if (mFocusTarget == FocusAddSystem || mFocusTarget == FocusAddCollection) {
        ButtonComponent* button {mFocusTarget == FocusAddSystem ? mAddSystem.get() :
                                                                  mAddCollection.get()};
        if (input.value == 0)
            return false;
        if (config->isMappedLike("up", input)) {
            const int target {nextTarget(mFocusTarget, -1)};
            if (target >= 0) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                moveFocus(target);
            }
            return true;
        }
        if (config->isMappedLike("down", input)) {
            const int target {nextTarget(mFocusTarget, 1)};
            if (target >= 0) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                moveFocus(target);
            }
            return true;
        }
        if (config->isMappedTo("a", input))
            return button->input(config, input);
        return false;
    }

    // A grid is focused.
    MadTileGrid* grid {mFocusTarget == FocusSystemGrid ? mSystemGrid.get() :
                                                         mCollectionGrid.get()};
    if (grid == nullptr) {
        moveFocus(FocusAddSystem);
        return true;
    }
    if (input.value != 0 && config->isMappedLike("up", input)) {
        const int before {grid->cursorIndex()};
        grid->input(config, input);
        if (grid->cursorIndex() == before) {
            const int target {nextTarget(mFocusTarget, -1)}; // Top row: leave.
            if (target >= 0) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                moveFocus(target);
            }
        }
        else {
            followFocus();
        }
        return true;
    }
    if (input.value != 0 && config->isMappedLike("down", input)) {
        const int before {grid->cursorIndex()};
        grid->input(config, input);
        if (grid->cursorIndex() != before) {
            followFocus();
            return true;
        }
        const int target {nextTarget(mFocusTarget, 1)}; // Bottom row: leave.
        if (target >= 0) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            moveFocus(target);
        }
        return true;
    }
    if (grid->input(config, input)) {
        followFocus();
        return true;
    }
    return false;
}

void GuiMadPagePriority::pageScroll(int direction)
{
    if (!mBuilt || mScroll == nullptr)
        return;
    // Targets: the two add buttons + every grid row of both grids.
    std::vector<PagedTarget> targets;
    targets.push_back({FocusAddSystem, -1, mAddSystem->getPosition().y,
                       mAddSystem->getPosition().y + mAddSystem->getSize().y});
    if (mSystemGrid != nullptr) {
        for (int row {0}; row < mSystemGrid->rows(); ++row) {
            const glm::vec2 rect {mSystemGrid->rowRect(row)};
            targets.push_back({FocusSystemGrid, row, mSystemGrid->getPosition().y + rect.x,
                               mSystemGrid->getPosition().y + rect.y});
        }
    }
    targets.push_back({FocusAddCollection, -1, mAddCollection->getPosition().y,
                       mAddCollection->getPosition().y + mAddCollection->getSize().y});
    if (mCollectionGrid != nullptr) {
        for (int row {0}; row < mCollectionGrid->rows(); ++row) {
            const glm::vec2 rect {mCollectionGrid->rowRect(row)};
            targets.push_back({FocusCollectionGrid, row,
                               mCollectionGrid->getPosition().y + rect.x,
                               mCollectionGrid->getPosition().y + rect.y});
        }
    }

    bool moved {false};
    if (mScroll->overflows())
        moved = mScroll->pageScroll(direction);
    const float viewTop {mScroll->overflows() ? mScroll->scrollOffset() : 0.0f};
    const float viewBottom {viewTop + (mScroll->overflows() ? mScroll->getSize().y :
                                                              mScroll->contentHeight())};
    const int pick {pickPagedTarget(targets, direction, viewTop, viewBottom)};
    if (pick >= 0) {
        const PagedTarget& target {targets[pick]};
        bool changed {target.id != mFocusTarget};
        if (target.id == FocusSystemGrid || target.id == FocusCollectionGrid) {
            MadTileGrid* grid {target.id == FocusSystemGrid ? mSystemGrid.get() :
                                                              mCollectionGrid.get()};
            const int columns {std::max(1, grid->columns())};
            const int column {grid->cursorIndex() % columns};
            const int index {
                std::min(target.aux * columns + column, grid->tileCount() - 1)};
            if (index != grid->cursorIndex()) {
                grid->setCursorIndex(index);
                changed = true;
            }
        }
        setFocusTarget(target.id);
        followFocus();
        if (changed)
            moved = true;
    }
    if (moved)
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
}

std::vector<HelpPrompt> GuiMadPagePriority::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (!mBuilt)
        return prompts;
    if (mFocusTarget == FocusSystemGrid && mSystemGrid != nullptr)
        prompts = mSystemGrid->getHelpPrompts();
    else if (mFocusTarget == FocusCollectionGrid && mCollectionGrid != nullptr)
        prompts = mCollectionGrid->getHelpPrompts();
    else {
        prompts.push_back(HelpPrompt("up/down", "choose"));
        prompts.push_back(HelpPrompt("a", "select"));
    }
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void GuiMadPagePriority::onSaveFocus()
{
    mFocusCookie = mFocusTarget;
    if (mSystemGrid != nullptr)
        mSystemGridCookie = mSystemGrid->cursorIndex();
    if (mCollectionGrid != nullptr)
        mCollectionGridCookie = mCollectionGrid->cursorIndex();
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void GuiMadPagePriority::onRestoreFocus()
{
    if (!mBuilt)
        return;
    mFocusTarget = mFocusCookie;
    // rebuild() (triggered by onChildPopped right after) re-applies the rest.
}

//  ── GuiMadPagePriorityPicker ──

GuiMadPagePriorityPicker::GuiMadPagePriorityPicker(GuiMadPanel* panel, const std::string& kind)
    : MadPage {panel, kind == "system" ? "PICK A SYSTEM" : "PICK A COLLECTION"}
    , mKind {kind}
{
}

void GuiMadPagePriorityPicker::build()
{
    mIntro = std::make_shared<TextComponent>(
        mKind == "system" ?
            "Pick a system to set its controller priority (systems you have games for)." :
            "Pick an ES-DE custom collection to give it a controller rule that overrides "
            "the system rule for its member games. Enable collections in ES-DE first.",
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(mViewportPos.x, mViewportPos.y);
    mIntro->setSize(mViewportSize.x, 0.0f);
    addChild(mIntro.get());

    refreshList();
}

void GuiMadPagePriorityPicker::onChildPopped()
{
    // The editor's SAVE makes the picked entry unavailable — re-derive (the
    // Tk picker re-rendered fresh on every back()).
    refreshList();
}

void GuiMadPagePriorityPicker::refreshList()
{
    const int cursor {mGrid != nullptr ? mGrid->cursorIndex() : mFocusCookie};
    if (mGrid != nullptr) {
        removeChild(mGrid.get());
        mGrid.reset();
    }
    setLoadingText("Loading…");
    pageRequest(
        "priority.list", nullptr,
        [this, cursor](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load the priority rules: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            const rapidjson::Value& avail {MadJson::getMember(
                payload, mKind == "system" ? "available_systems" : "available_collections")};
            std::vector<MadTileGrid::Tile> tiles;
            if (avail.IsArray()) {
                for (rapidjson::SizeType i {0}; i < avail.Size(); ++i) {
                    MadTileGrid::Tile tile;
                    tile.key = MadJson::getString(avail[i], "name");
                    tile.label = tile.key;
                    tile.artPath = MadJson::getString(avail[i], "art");
                    tiles.emplace_back(tile);
                }
            }
            if (tiles.empty()) {
                setLoadingText(mKind == "system" ?
                                   "(no other systems with games found)" :
                                   "(no enabled custom collections — create/enable one in "
                                   "ES-DE)");
                mPanel->refreshHelpPrompts();
                return;
            }
            const float top {mIntro->getPosition().y + mIntro->getSize().y +
                             Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f};
            mGrid = std::make_shared<MadTileGrid>();
            mGrid->setPosition(mViewportPos.x, top);
            mGrid->setSize(mViewportSize.x, mViewportPos.y + mViewportSize.y - top);
            mGrid->setTiles(tiles);
            mGrid->setOnPick([this](const std::string& name) {
                mPanel->pushPage(new GuiMadPagePriorityEdit(mPanel, name, mKind));
            });
            mGrid->setCursorIndex(cursor);
            mGrid->onFocusGained();
            addChild(mGrid.get());
            mPanel->refreshHelpPrompts();
        },
        10000);
}

bool GuiMadPagePriorityPicker::input(InputConfig* config, Input input)
{
    if (mGrid != nullptr)
        return mGrid->input(config, input);
    return false;
}

void GuiMadPagePriorityPicker::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPagePriorityPicker::getHelpPrompts()
{
    if (mGrid != nullptr)
        return mGrid->getHelpPrompts();
    return std::vector<HelpPrompt>();
}

void GuiMadPagePriorityPicker::onSaveFocus()
{
    if (mGrid != nullptr)
        mFocusCookie = mGrid->cursorIndex();
}

void GuiMadPagePriorityPicker::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mFocusCookie);
}

//  ── GuiMadPagePriorityEdit ──

GuiMadPagePriorityEdit::GuiMadPagePriorityEdit(GuiMadPanel* panel,
                                               const std::string& name,
                                               const std::string& kind)
    : MadPage {panel, "PRIORITY: " + Utils::String::toUpper(name)}
    , mName {name}
    , mKind {kind}
    , mNports {2}
    , mLightgun {false}
    , mFocusTarget {FocusList}
    , mBuilt {false}
{
}

void GuiMadPagePriorityEdit::build()
{
    setLoadingText("Loading…");
    const std::string name {mName};
    const std::string kind {mKind};
    pageRequest(
        "priority.get",
        [name, kind](MadJson::Writer& writer) {
            writer.Key("name");
            writer.String(name.c_str(), static_cast<rapidjson::SizeType>(name.length()));
            writer.Key("kind");
            writer.String(kind.c_str(), static_cast<rapidjson::SizeType>(kind.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load the rule: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            rebuild(payload);
        });
}

void GuiMadPagePriorityEdit::rebuild(const rapidjson::Value& result)
{
    mNports = MadJson::getInt(result, "nports", 2);
    mLightgun = MadJson::getBool(result, "require_sinden");

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    const float miniHeight {Font::get(FONT_SIZE_MINI)->getHeight()};

    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};

    bool hasXArcade {false};
    {
        const rapidjson::Value& oa {MadJson::getMember(result, "order")};
        if (oa.IsArray())
            for (rapidjson::SizeType i {0}; i < oa.Size(); ++i)
                if (oa[i].IsString() && std::string {oa[i].GetString()} == "X-Arcade")
                    hasXArcade = true;
    }
    std::string hintText {"Reorder the families below (top = Player 1): A lifts a row, up/down "
                          "move it, A drops it. Then Save."};
    if (hasXArcade)
        hintText += "  Note: the X-Arcade is ONE device that fills BOTH Player 1 and Player 2 "
                    "(its two halves) — put it at the top and P1+P2 are both covered; the family "
                    "below it is only used when no X-Arcade is connected.";
    mHint = std::make_shared<TextComponent>(
        hintText,
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mHint->setPosition(0.0f, y);
    mHint->setSize(mViewportSize.x, 0.0f);
    mScroll->addChild(mHint.get());
    y += mHint->getSize().y + smallHeight * 0.4f;

    if (mKind == "collection") {
        mLightgunChip = std::make_shared<MadChipRow>();
        mLightgunChip->setPosition(0.0f, y);
        mLightgunChip->setSize(mViewportSize.x, 1.0f);
        mLightgunChip->setChips({{"lightgun", "lightgun (Sinden)", mLightgun}});
        mLightgunChip->setSize(mViewportSize.x,
                               std::max(1.0f, mLightgunChip->contentHeight()));
        // Local state only — saved together with the order, like the Tk page.
        mLightgunChip->setOnToggle(
            [this](const std::string&, const bool on) { mLightgun = on; });
        mScroll->addChild(mLightgunChip.get());
        y += mLightgunChip->getSize().y + smallHeight * 0.15f;

        mLightgunNote = std::make_shared<TextComponent>(
            "  lightgun = require a Sinden gun and pin its aim; the order below is the "
            "menu / coin / start joypads.",
            Font::get(FONT_SIZE_MINI), MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER,
            glm::ivec2 {0, 1});
        mLightgunNote->setPosition(0.0f, y);
        mLightgunNote->setSize(mViewportSize.x, 0.0f);
        mScroll->addChild(mLightgunNote.get());
        y += mLightgunNote->getSize().y + smallHeight * 0.4f;
    }
    (void)miniHeight;

    std::vector<std::string> order;
    const rapidjson::Value& orderArr {MadJson::getMember(result, "order")};
    if (orderArr.IsArray()) {
        for (rapidjson::SizeType i {0}; i < orderArr.Size(); ++i) {
            if (orderArr[i].IsString())
                order.emplace_back(orderArr[i].GetString());
        }
    }
    mList = std::make_shared<MadReorderList>();
    mList->setPosition(0.0f, y);
    mList->setSize(mViewportSize.x * 0.6f, 1.0f);
    mList->setItems(order);
    mList->setSize(mViewportSize.x * 0.6f, std::max(1.0f, mList->contentHeight()));
    mScroll->addChild(mList.get());
    y += mList->getSize().y + smallHeight * 0.5f;

    mSaveButton = std::make_shared<ButtonComponent>("SAVE", "save", [this] { save(); });
    mSaveButton->setPosition(0.0f, y);
    mScroll->addChild(mSaveButton.get());
    mClearButton = std::make_shared<ButtonComponent>("CLEAR RULE", "clear rule",
                                                     [this] { clearRule(); });
    mClearButton->setPosition(mSaveButton->getSize().x + mViewportSize.x * 0.012f, y);
    mScroll->addChild(mClearButton.get());
    y += mSaveButton->getSize().y;

    mScroll->setContentHeight(y + smallHeight * 0.5f);

    mBuilt = true;
    setFocusTarget(mKind == "collection" ? FocusLightgun : FocusList);
    followFocus();
}

void GuiMadPagePriorityEdit::save()
{
    const std::string name {mName};
    const std::string kind {mKind};
    const std::vector<std::string> order {mList->items()};
    const int nports {mNports};
    const bool lightgun {mLightgun};
    pageRequest(
        "policy.set_ports",
        [name, kind, order, nports, lightgun](MadJson::Writer& writer) {
            writer.Key("name");
            writer.String(name.c_str(), static_cast<rapidjson::SizeType>(name.length()));
            writer.Key("kind");
            writer.String(kind.c_str(), static_cast<rapidjson::SizeType>(kind.length()));
            writer.Key("order");
            writer.StartArray();
            for (const std::string& family : order)
                writer.String(family.c_str(),
                              static_cast<rapidjson::SizeType>(family.length()));
            writer.EndArray();
            writer.Key("nports");
            writer.Int(nports);
            if (kind == "collection") {
                writer.Key("require_sinden");
                writer.Bool(lightgun);
            }
        },
        [this, order](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't save " + mName + ": " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            footer()->flash("Saved " + mName + ": P1 → " +
                            (order.empty() ? "(empty)" : order[0]) +
                            ". Applies on the next game launch (no ES-DE restart).");
        });
}

void GuiMadPagePriorityEdit::clearRule()
{
    const std::string name {mName};
    const std::string kind {mKind};
    pageRequest(
        "policy.clear_ports",
        [name, kind](MadJson::Writer& writer) {
            writer.Key("name");
            writer.String(name.c_str(), static_cast<rapidjson::SizeType>(name.length()));
            writer.Key("kind");
            writer.String(kind.c_str(), static_cast<rapidjson::SizeType>(kind.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't clear the rule: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            footer()->flash("Rule cleared — " + mName + " uses the default order");
            mPanel->popPage(); // 'this' dies here; nothing below touches members.
        });
}

void GuiMadPagePriorityEdit::setFocusTarget(const int target)
{
    mFocusTarget = target;
    if (mLightgunChip != nullptr) {
        if (target == FocusLightgun)
            mLightgunChip->onFocusGained();
        else
            mLightgunChip->onFocusLost();
    }
    if (mList != nullptr) {
        if (target == FocusList)
            mList->onFocusGained();
        else
            mList->onFocusLost();
    }
    auto applyButton = [target](const std::shared_ptr<ButtonComponent>& button,
                                const int focusId) {
        if (button == nullptr)
            return;
        if (target == focusId)
            button->onFocusGained();
        else
            button->onFocusLost();
    };
    applyButton(mSaveButton, FocusSave);
    applyButton(mClearButton, FocusClear);
    mPanel->refreshHelpPrompts();
}

void GuiMadPagePriorityEdit::moveFocus(const int target)
{
    setFocusTarget(target);
    followFocus();
}

void GuiMadPagePriorityEdit::followFocus()
{
    if (mScroll == nullptr)
        return;
    float top {0.0f};
    float bottom {0.0f};
    switch (mFocusTarget) {
        case FocusLightgun: {
            top = 0.0f; // Topmost: reveal the hint.
            bottom = mLightgunChip->getPosition().y + mLightgunChip->getSize().y;
            break;
        }
        case FocusList: {
            const glm::vec2 row {mList->cursorRowRect()};
            top = mList->getPosition().y + row.x;
            bottom = mList->getPosition().y + row.y;
            break;
        }
        case FocusSave:
        case FocusClear: {
            top = mSaveButton->getPosition().y;
            bottom = top + mSaveButton->getSize().y;
            break;
        }
        default:
            return;
    }
    mScroll->ensureVisible(top, bottom);
}

bool GuiMadPagePriorityEdit::onBackPressed()
{
    if (mList != nullptr && mList->carrying()) {
        mList->cancelCarry();
        mPanel->refreshHelpPrompts();
        return true;
    }
    return false;
}

bool GuiMadPagePriorityEdit::input(InputConfig* config, Input input)
{
    if (!mBuilt)
        return false;

    if (mFocusTarget == FocusLightgun) {
        if (mLightgunChip->input(config, input))
            return true;
        if (input.value != 0 && config->isMappedLike("down", input)) {
            moveFocus(FocusList);
            return true;
        }
        if (input.value != 0 && config->isMappedLike("up", input))
            return true; // Top edge.
        return false;
    }

    if (mFocusTarget == FocusList) {
        if (mList->input(config, input)) {
            followFocus(); // Cursor (or the carried row) moved.
            mPanel->refreshHelpPrompts(); // Carry state changes the prompts.
            return true;
        }
        if (input.value == 0)
            return false;
        if (config->isMappedLike("up", input)) {
            // Carry never leaves the list — overshooting up while moving a
            // row to P1 must not land focus on the lightgun chip.
            if (!mList->carrying() && mLightgunChip != nullptr)
                moveFocus(FocusLightgun);
            return true;
        }
        if (config->isMappedLike("down", input)) {
            if (!mList->carrying())
                moveFocus(FocusSave);
            return true;
        }
        return false;
    }

    // Save / Clear row.
    if (input.value == 0)
        return false;
    if (config->isMappedLike("left", input)) {
        if (mFocusTarget == FocusClear) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            setFocusTarget(FocusSave);
        }
        return true;
    }
    if (config->isMappedLike("right", input)) {
        if (mFocusTarget == FocusSave) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            setFocusTarget(FocusClear);
        }
        return true;
    }
    if (config->isMappedLike("up", input)) {
        moveFocus(FocusList);
        return true;
    }
    if (config->isMappedLike("down", input))
        return true; // Bottom edge.
    if (config->isMappedTo("a", input)) {
        return mFocusTarget == FocusSave ? mSaveButton->input(config, input) :
                                           mClearButton->input(config, input);
    }
    return false;
}

void GuiMadPagePriorityEdit::pageScroll(int direction)
{
    if (!mBuilt || mScroll == nullptr || !mScroll->overflows())
        return;
    // Pure view scroll (no followFocus — it would snap straight back to the
    // focused control and undo the page step); the next d-pad move re-snaps.
    if (mScroll->pageScroll(direction))
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
}

std::vector<HelpPrompt> GuiMadPagePriorityEdit::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (!mBuilt)
        return prompts;
    if (mFocusTarget == FocusList && mList != nullptr) {
        prompts = mList->getHelpPrompts();
    }
    else if (mFocusTarget == FocusLightgun) {
        prompts.push_back(HelpPrompt("a", "toggle"));
        prompts.push_back(HelpPrompt("up/down", "choose"));
    }
    else {
        prompts.push_back(HelpPrompt("left/right", "choose"));
        prompts.push_back(HelpPrompt("a", "select"));
        prompts.push_back(HelpPrompt("up/down", "choose"));
    }
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}
