//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRAControllers.cpp
//
//  MAD control panel: RetroArch hub -> Controllers section (deck-patches).
//

#include "guis/mad/pages/GuiMadPageRAControllers.h"

#include "Sound.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPagePriority.h" // GuiMadPagePriorityPicker/Edit (system/collection scopes)

#include <algorithm>

namespace
{
    std::string joinComma(const std::vector<std::string>& items)
    {
        std::string out;
        for (size_t i {0}; i < items.size(); ++i) {
            if (i > 0)
                out += ", ";
            out += items[i];
        }
        return out;
    }

    // Identical to the (private, file-local) helper in GuiMadPagePriority.cpp;
    // duplicated rather than shared since it's an anonymous-namespace detail there.
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

GuiMadPageRAControllers::GuiMadPageRAControllers(GuiMadPanel* panel, const std::string& title)
    : MadPage {panel, title}
    , mNports {2}
    , mFocusTarget {FocusToggles}
    , mSystemGridCookie {0}
    , mCollectionGridCookie {0}
    , mScrollCookie {0.0f}
    , mBuilt {false}
{
}

void GuiMadPageRAControllers::build()
{
    setLoadingText("Loading RetroArch controllers…");
    pageRequest(
        "racontrollers.get",
        [](MadJson::Writer& writer) {
            writer.Key("scope");
            writer.String("global", 6);
            writer.Key("name");
            writer.String("", 0);
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                setLoadingText("");
                footer()->setStatus("Couldn't load RetroArch controllers: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            // Copy the primitives out: `payload` doesn't survive past this
            // callback, and the systems/collections list below is a second
            // async round trip.
            mGlobalOrder.clear();
            const rapidjson::Value& orderArr {MadJson::getMember(payload, "order")};
            if (orderArr.IsArray()) {
                for (rapidjson::SizeType i {0}; i < orderArr.Size(); ++i)
                    if (orderArr[i].IsString())
                        mGlobalOrder.emplace_back(orderArr[i].GetString());
            }
            mNports = MadJson::getInt(payload, "nports", 2);
            mConnectedFamilies.clear();
            const rapidjson::Value& connArr {MadJson::getMember(payload, "connected_families")};
            if (connArr.IsArray()) {
                for (rapidjson::SizeType i {0}; i < connArr.Size(); ++i)
                    if (connArr[i].IsString())
                        mConnectedFamilies.emplace_back(connArr[i].GetString());
            }
            mToggleItems.clear();
            const rapidjson::Value& toggleArr {MadJson::getMember(payload, "toggles")};
            if (toggleArr.IsArray()) {
                for (rapidjson::SizeType i {0}; i < toggleArr.Size(); ++i) {
                    const rapidjson::Value& t {toggleArr[i]};
                    mToggleItems.push_back(MadToggleList::Item {MadJson::getString(t, "key"),
                                                                MadJson::getString(t, "label"),
                                                                MadJson::getBool(t, "value")});
                }
            }
            requestSystemsList();
        },
        10000);
}

void GuiMadPageRAControllers::requestSystemsList()
{
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

void GuiMadPageRAControllers::onChildPopped()
{
    build(); // A picker/editor may have added, changed, or cleared a rule.
}

void GuiMadPageRAControllers::rebuild(const rapidjson::Value& result)
{
    if (mSystemGrid != nullptr)
        mSystemGridCookie = mSystemGrid->cursorIndex();
    if (mCollectionGrid != nullptr)
        mCollectionGridCookie = mCollectionGrid->cursorIndex();
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
    // Children first (dtors self-detach), then the scroll view.
    mIntro.reset();
    mConnectedLine.reset();
    mToggleList.reset();
    mHint.reset();
    mGlobalList.reset();
    mSaveButton.reset();
    mClearButton.reset();
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
        "Preferred controller per system (top = Player 1). RetroArch systems only; "
        "standalone emulators are configured on the Backends page. A custom COLLECTION rule "
        "overrides the system rule for its member games (e.g. a lightgun collection).",
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(0.0f, y);
    mIntro->setSize(mViewportSize.x, 0.0f);
    mScroll->addChild(mIntro.get());
    y += mIntro->getSize().y + smallHeight * 0.4f;

    //  -- Global scope, inline --

    mConnectedLine = std::make_shared<TextComponent>(
        "Connected: " +
            (mConnectedFamilies.empty() ? std::string("(none)") : joinComma(mConnectedFamilies)),
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mConnectedLine->setPosition(0.0f, y);
    mConnectedLine->setSize(mViewportSize.x, 0.0f);
    mScroll->addChild(mConnectedLine.get());
    y += mConnectedLine->getSize().y + smallHeight * 0.4f;

    // Warn toggles (global scope only): a self-contained focus target, the
    // sibling of the reorder list below. Its onToggle writes policy.set_scope_flag.
    if (!mToggleItems.empty()) {
        mToggleList = std::make_shared<MadToggleList>();
        mToggleList->setPosition(0.0f, y);
        mToggleList->setSize(mViewportSize.x, 1.0f);
        mToggleList->setItems(mToggleItems);
        mToggleList->setSize(mViewportSize.x, std::max(1.0f, mToggleList->contentHeight()));
        mToggleList->setOnToggle(
            [this](int, const std::string& key, bool value) { setScopeFlag(key, value); });
        mScroll->addChild(mToggleList.get());
        y += mToggleList->getSize().y + smallHeight * 0.4f;
    }

    bool hasXArcade {false};
    for (const std::string& fam : mGlobalOrder)
        if (fam == "X-Arcade")
            hasXArcade = true;
    std::string hintText {"Reorder the families below (top = Player 1): A lifts a row, up/down "
                          "move it, A drops it. Then Save."};
    if (hasXArcade)
        hintText += "  Note: the X-Arcade is ONE device that fills BOTH Player 1 and Player 2 "
                    "(its two halves), so put it at the top and P1+P2 are both covered; the "
                    "family below it is only used when no X-Arcade is connected.";
    mHint = std::make_shared<TextComponent>(
        hintText,
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mHint->setPosition(0.0f, y);
    mHint->setSize(mViewportSize.x, 0.0f);
    mScroll->addChild(mHint.get());
    y += mHint->getSize().y + smallHeight * 0.4f;

    mGlobalList = std::make_shared<MadReorderList>();
    mGlobalList->setPosition(0.0f, y);
    mGlobalList->setSize(mViewportSize.x * 0.6f, 1.0f);
    mGlobalList->setItems(mGlobalOrder);
    mGlobalList->setSize(mViewportSize.x * 0.6f, std::max(1.0f, mGlobalList->contentHeight()));
    mScroll->addChild(mGlobalList.get());
    y += mGlobalList->getSize().y + smallHeight * 0.5f;

    mSaveButton = std::make_shared<ButtonComponent>("SAVE", "save", [this] { saveGlobalOrder(); });
    mSaveButton->setPosition(0.0f, y);
    mScroll->addChild(mSaveButton.get());
    mClearButton = std::make_shared<ButtonComponent>("CLEAR RULE", "clear rule",
                                                     [this] { clearGlobalOrder(); });
    mClearButton->setPosition(mSaveButton->getSize().x + mViewportSize.x * 0.012f, y);
    mScroll->addChild(mClearButton.get());
    y += mSaveButton->getSize().y + smallHeight * 0.6f;

    //  -- Configured systems/collections (verbatim Priority-root structure) --

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
    // A cookied focus target may no longer exist. Reset a vanished grid to its
    // OWN add-button (local, like the Priority root), and a vanished toggle
    // block to the first surviving target.
    if (mFocusTarget == FocusSystemGrid && mSystemGrid == nullptr)
        mFocusTarget = FocusAddSystem;
    else if (mFocusTarget == FocusCollectionGrid && mCollectionGrid == nullptr)
        mFocusTarget = FocusAddCollection;
    else if (mFocusTarget == FocusToggles && mToggleList == nullptr) {
        const int first {nextTarget(FocusToggles - 1, 1)};
        mFocusTarget = first >= 0 ? first : FocusReorderList;
    }
    setFocusTarget(mFocusTarget);
    followFocus();
}

int GuiMadPageRAControllers::nextTarget(int target, const int direction) const
{
    while (true) {
        target += direction;
        if (target < FocusToggles || target > FocusCollectionGrid)
            return -1;
        if (target == FocusToggles && mToggleList == nullptr)
            continue;
        if (target == FocusSystemGrid && mSystemGrid == nullptr)
            continue;
        if (target == FocusCollectionGrid && mCollectionGrid == nullptr)
            continue;
        return target;
    }
}

void GuiMadPageRAControllers::setFocusTarget(const int target)
{
    mFocusTarget = target;
    if (mToggleList != nullptr) {
        if (target == FocusToggles)
            mToggleList->onFocusGained();
        else
            mToggleList->onFocusLost();
    }
    if (mGlobalList != nullptr) {
        if (target == FocusReorderList)
            mGlobalList->onFocusGained();
        else
            mGlobalList->onFocusLost();
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

void GuiMadPageRAControllers::moveFocus(const int target)
{
    setFocusTarget(target);
    followFocus();
}

void GuiMadPageRAControllers::followFocus()
{
    if (mScroll == nullptr)
        return;
    // The topmost EXISTING target reveals everything above it too (intro /
    // connected line) by snapping to top, but ONLY when its own cursor is on
    // row 0 (a lower row must follow the cursor, not jerk the view up).
    const int first {nextTarget(FocusToggles - 1, 1)};
    float top {0.0f};
    float bottom {0.0f};
    switch (mFocusTarget) {
        case FocusToggles: {
            if (mToggleList == nullptr)
                return;
            const glm::vec2 row {mToggleList->cursorRowRect()};
            const bool revealTop {mFocusTarget == first && mToggleList->cursorIndex() == 0};
            top = revealTop ? 0.0f : mToggleList->getPosition().y + row.x;
            bottom = mToggleList->getPosition().y + row.y;
            break;
        }
        case FocusReorderList: {
            if (mGlobalList == nullptr)
                return;
            const glm::vec2 row {mGlobalList->cursorRowRect()};
            const bool revealTop {mFocusTarget == first && mGlobalList->cursorIndex() == 0};
            top = revealTop ? 0.0f : mGlobalList->getPosition().y + row.x;
            bottom = mGlobalList->getPosition().y + row.y;
            break;
        }
        case FocusSave:
        case FocusClear: {
            top = mSaveButton->getPosition().y;
            bottom = top + mSaveButton->getSize().y;
            break;
        }
        case FocusAddSystem: {
            top = mAddSystem->getPosition().y;
            bottom = top + mAddSystem->getSize().y;
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

void GuiMadPageRAControllers::setScopeFlag(const std::string& flag, bool value)
{
    pageRequest(
        "policy.set_scope_flag",
        [flag, value](MadJson::Writer& writer) {
            writer.Key("kind");
            writer.String("global", 6);
            writer.Key("name");
            writer.String("", 0);
            writer.Key("flag");
            writer.String(flag.c_str(), static_cast<rapidjson::SizeType>(flag.length()));
            writer.Key("value");
            writer.Bool(value);
        },
        [this, flag, value](bool ok, const rapidjson::Value& payload) {
            // Resolve the human label once (toasts + the rollback lookup share it);
            // fall back to the raw policy key only if the row is somehow gone.
            const int idx {mToggleList != nullptr ? mToggleList->rowIndexOfKey(flag) : -1};
            const std::string label {idx >= 0 && idx < static_cast<int>(mToggleItems.size())
                                         ? mToggleItems[idx].label : flag};
            if (!ok) {
                // No backend clamp to re-sync from for a warn flag (unlike the
                // per-system toggles): the pre-toggle value IS the truth on
                // failure, so roll the switch back to it.
                if (idx >= 0)
                    mToggleList->setRowValue(idx, !value);
                footer()->flash("Couldn't save \"" + label + "\": " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            footer()->flash(value ? "Enabled \"" + label + "\"" :
                                    "Disabled \"" + label + "\"");
        });
}

void GuiMadPageRAControllers::saveGlobalOrder()
{
    const std::vector<std::string> order {mGlobalList->items()};
    const int nports {mNports};
    pageRequest(
        "policy.set_ports",
        [order, nports](MadJson::Writer& writer) {
            writer.Key("kind");
            writer.String("global", 6);
            writer.Key("name");
            writer.String("", 0);
            writer.Key("order");
            writer.StartArray();
            for (const std::string& family : order)
                writer.String(family.c_str(),
                              static_cast<rapidjson::SizeType>(family.length()));
            writer.EndArray();
            writer.Key("nports");
            writer.Int(nports);
        },
        [this, order](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't save the global order: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            // The on-screen order already equals what was saved, so (unlike
            // clear) no rebuild is needed: just confirm, like the Priority editor.
            footer()->flash("Saved the global order: P1 = " +
                            (order.empty() ? "(empty)" : order[0]) +
                            ". Applies on the next game launch (no ES-DE restart).");
        });
}

void GuiMadPageRAControllers::clearGlobalOrder()
{
    pageRequest(
        "policy.clear_ports",
        [](MadJson::Writer& writer) {
            writer.Key("kind");
            writer.String("global", 6);
            writer.Key("name");
            writer.String("", 0);
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't clear the global order: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            footer()->flash("Global order cleared, the default order applies");
            build(); // Re-fetch: the order reverts to the family default.
        });
}

bool GuiMadPageRAControllers::onBackPressed()
{
    if (mGlobalList != nullptr && mGlobalList->carrying()) {
        mGlobalList->cancelCarry();
        mPanel->refreshHelpPrompts();
        return true;
    }
    return false;
}

bool GuiMadPageRAControllers::input(InputConfig* config, Input input)
{
    if (!mBuilt)
        return false;

    if (mFocusTarget == FocusToggles) {
        // The toggle list owns its own up/down cursor + A toggle; it returns
        // false at the top/bottom edge so the page can move focus away.
        if (mToggleList != nullptr && mToggleList->input(config, input)) {
            followFocus();
            return true;
        }
        if (input.value == 0)
            return false;
        if (config->isMappedLike("up", input)) {
            const int target {nextTarget(FocusToggles, -1)}; // -1 at the top edge: stay put.
            if (target >= 0) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                moveFocus(target);
            }
            return true;
        }
        if (config->isMappedLike("down", input)) {
            const int target {nextTarget(FocusToggles, 1)};
            if (target >= 0) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                moveFocus(target);
            }
            return true;
        }
        return false;
    }

    if (mFocusTarget == FocusReorderList) {
        if (mGlobalList->input(config, input)) {
            followFocus(); // Cursor (or the carried row) moved.
            mPanel->refreshHelpPrompts(); // Carry state changes the prompts.
            return true;
        }
        if (input.value == 0)
            return false;
        if (config->isMappedLike("up", input)) {
            if (!mGlobalList->carrying()) {
                const int target {nextTarget(FocusReorderList, -1)};
                if (target >= 0) {
                    NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                    moveFocus(target);
                }
            }
            return true;
        }
        if (config->isMappedLike("down", input)) {
            if (!mGlobalList->carrying()) {
                const int target {nextTarget(FocusReorderList, 1)};
                if (target >= 0) {
                    NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                    moveFocus(target);
                }
            }
            return true;
        }
        return false;
    }

    if (mFocusTarget == FocusSave || mFocusTarget == FocusClear) {
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
            const int target {nextTarget(FocusSave, -1)}; // Save is the row's low index.
            if (target >= 0) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                moveFocus(target);
            }
            return true;
        }
        if (config->isMappedLike("down", input)) {
            // Anchor on the LAST member of the Save/Clear pair so DOWN descends
            // PAST it into the configured-systems block (nextTarget(FocusSave,1)
            // would just return FocusClear, leaving the lower section unreachable).
            const int target {nextTarget(FocusClear, 1)};
            if (target >= 0) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                moveFocus(target);
            }
            return true;
        }
        if (config->isMappedTo("a", input)) {
            return mFocusTarget == FocusSave ? mSaveButton->input(config, input) :
                                               mClearButton->input(config, input);
        }
        return false;
    }

    if (mFocusTarget == FocusAddSystem || mFocusTarget == FocusAddCollection) {
        ButtonComponent* button {mFocusTarget == FocusAddSystem ? mAddSystem.get() :
                                                                  mAddCollection.get()};
        if (input.value == 0)
            return false;
        if (config->isMappedLike("up", input)) {
            int target {nextTarget(mFocusTarget, -1)};
            // Entering the Save/Clear pair from below lands on the primary (Save),
            // not whichever button nextTarget's index math happens to reach.
            if (target == FocusClear)
                target = FocusSave;
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

void GuiMadPageRAControllers::pageScroll(int direction)
{
    if (!mBuilt || mScroll == nullptr)
        return;
    // A live reorder carry owns up/down (they move the carried row); paging away
    // would leave the carry live but invisible. Consume the page input while
    // carrying so the user must drop (A) or cancel (B) first.
    if (mGlobalList != nullptr && mGlobalList->carrying())
        return;
    std::vector<PagedTarget> targets;
    if (mToggleList != nullptr) {
        targets.push_back({FocusToggles, -1, mToggleList->getPosition().y,
                           mToggleList->getPosition().y + mToggleList->getSize().y});
    }
    if (mGlobalList != nullptr) {
        targets.push_back({FocusReorderList, -1, mGlobalList->getPosition().y,
                           mGlobalList->getPosition().y + mGlobalList->getSize().y});
    }
    targets.push_back({FocusSave, -1, mSaveButton->getPosition().y,
                       mSaveButton->getPosition().y + mSaveButton->getSize().y});
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

std::vector<HelpPrompt> GuiMadPageRAControllers::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (!mBuilt)
        return prompts;
    if (mFocusTarget == FocusToggles && mToggleList != nullptr)
        prompts = mToggleList->getHelpPrompts();
    else if (mFocusTarget == FocusReorderList && mGlobalList != nullptr)
        prompts = mGlobalList->getHelpPrompts();
    else if (mFocusTarget == FocusSave || mFocusTarget == FocusClear) {
        prompts.push_back(HelpPrompt("left/right", "choose"));
        prompts.push_back(HelpPrompt("a", "select"));
        prompts.push_back(HelpPrompt("up/down", "choose"));
    }
    else if (mFocusTarget == FocusSystemGrid && mSystemGrid != nullptr)
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

void GuiMadPageRAControllers::onSaveFocus()
{
    mFocusCookie = mFocusTarget;
    if (mSystemGrid != nullptr)
        mSystemGridCookie = mSystemGrid->cursorIndex();
    if (mCollectionGrid != nullptr)
        mCollectionGridCookie = mCollectionGrid->cursorIndex();
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void GuiMadPageRAControllers::onRestoreFocus()
{
    if (!mBuilt)
        return;
    mFocusTarget = mFocusCookie;
    // rebuild() (triggered by onChildPopped right after) re-applies the rest.
}
