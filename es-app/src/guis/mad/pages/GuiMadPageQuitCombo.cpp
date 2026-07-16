//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageQuitCombo.cpp
//
//  MAD control panel: Quit-game combo section (deck-patches).
//

#include "guis/mad/pages/GuiMadPageQuitCombo.h"

#include "Sound.h"
#include "Window.h"
#include "guis/mad/GuiMadCaptureModal.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "utils/StringUtil.h"

#include <cstdio>
#include "guis/mad/MadTheme.h"

namespace
{
    std::string joinNames(const std::vector<std::string>& names)
    {
        if (names.empty())
            return "(none)";
        std::string out;
        for (size_t i {0}; i < names.size(); ++i) {
            if (i > 0)
                out.append("+");
            out.append(names[i]);
        }
        return out;
    }

    std::string formatHold(const float value)
    {
        char buffer[16];
        snprintf(buffer, sizeof(buffer), "%.1f", value);
        return std::string {buffer};
    }
} // namespace

GuiMadPageQuitCombo::GuiMadPageQuitCombo(GuiMadPanel* panel)
    : MadPage {panel, "QUIT-GAME COMBO"}
    , mHold {1.0f}
    , mFocusTarget {FocusAdd}
    , mGridCookie {0}
    , mCollGridCookie {0}
    , mScrollCookie {0.0f}
    , mBuilt {false}
{
}

void GuiMadPageQuitCombo::build()
{
    setLoadingText("Loading quit combos…");

    // Console art first (one-time), then the page data.
    pageRequest(
        "systems.list", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            if (ok) {
                const rapidjson::Value& systems {MadJson::getMember(payload, "systems")};
                if (systems.IsArray()) {
                    for (rapidjson::SizeType i {0}; i < systems.Size(); ++i) {
                        mSystemArt[MadJson::getString(systems[i], "name")] =
                            MadJson::getString(systems[i], "art");
                    }
                }
            }
            refreshData();
        },
        10000);
}

void GuiMadPageQuitCombo::refreshData(const bool keepUnsaved)
{
    pageRequest("quitcombo.get", nullptr,
                [this, keepUnsaved](bool ok, const rapidjson::Value& payload) {
                    setLoadingText("");
                    if (!ok) {
                        footer()->setStatus(
                            "Couldn't load the quit combos: " +
                                MadJson::getString(payload, "message", "unknown error"),
                            true);
                        return;
                    }
                    rebuild(payload, keepUnsaved);
                });
}

void GuiMadPageQuitCombo::onChildPopped()
{
    // The picker/detail pages write per-system overrides; rebuild from truth —
    // but keep the in-memory global combo/hold time: an unsaved DETECT result
    // must survive a trip into the per-system pages.
    refreshData(true);
}

std::string GuiMadPageQuitCombo::comboString() const
{
    return joinNames(mComboNames);
}

void GuiMadPageQuitCombo::clearLayout()
{
    // Children first: each ~GuiComponent detaches itself from the LIVE scroll
    // view. (removeChild() on the wrong parent nulls the child's parent but
    // erases from the wrong list — the view would keep a dangling pointer.)
    mIntro.reset();
    mGlobalHeader.reset();
    mComboLine.reset();
    mStepper.reset();
    mDetectButton.reset();
    mSaveButton.reset();
    mPerSystemHeader.reset();
    mWiiNote.reset();
    mAddButton.reset();
    mNoOverrides.reset();
    mGrid.reset();
    mPerCollHeader.reset();
    mAddCollButton.reset();
    mNoCollOverrides.reset();
    mCollGrid.reset();
    if (mScroll != nullptr) {
        removeChild(mScroll.get());
        mScroll.reset();
    }
}

void GuiMadPageQuitCombo::rebuild(const rapidjson::Value& result, const bool keepUnsaved)
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
    if (mCollGrid != nullptr)
        mCollGridCookie = mCollGrid->cursorIndex();
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
    clearLayout();

    // ── data ──
    // keepUnsaved (post-child-pop refresh): the global combo/hold time fields
    // may hold an unsaved DETECT/stepper edit — only the overrides re-read.
    if (!keepUnsaved) {
        mComboButtons.clear();
        mComboNames.clear();
        const rapidjson::Value& buttons {MadJson::getMember(result, "buttons")};
        if (buttons.IsArray()) {
            for (rapidjson::SizeType i {0}; i < buttons.Size(); ++i) {
                if (buttons[i].IsInt())
                    mComboButtons.emplace_back(buttons[i].GetInt());
            }
        }
        const rapidjson::Value& names {MadJson::getMember(result, "names")};
        if (names.IsArray()) {
            for (rapidjson::SizeType i {0}; i < names.Size(); ++i) {
                if (names[i].IsString())
                    mComboNames.emplace_back(names[i].GetString());
            }
        }
        const rapidjson::Value& hold {MadJson::getMember(result, "hold_sec")};
        mHold = hold.IsNumber() ? static_cast<float>(hold.GetDouble()) : 1.0f;
        // Clean baseline for the buffered global combo. Gated on !keepUnsaved so
        // a post-child-pop refresh preserves an unsaved DETECT/hold edit's dirt.
        mBaselineButtons = mComboButtons;
        mBaselineHold = mHold;
    }

    mOverrides.clear();
    const rapidjson::Value& overrides {MadJson::getMember(result, "overrides")};
    if (overrides.IsObject()) {
        for (auto it = overrides.MemberBegin(); it != overrides.MemberEnd(); ++it) {
            std::vector<std::string> overrideNames;
            const rapidjson::Value& list {MadJson::getMember(it->value, "names")};
            if (list.IsArray()) {
                for (rapidjson::SizeType i {0}; i < list.Size(); ++i) {
                    if (list[i].IsString())
                        overrideNames.emplace_back(list[i].GetString());
                }
            }
            mOverrides.emplace_back(it->name.GetString(), joinNames(overrideNames));
        }
    }

    mCollArt.clear();
    const rapidjson::Value& collList {MadJson::getMember(result, "collections")};
    if (collList.IsArray()) {
        for (rapidjson::SizeType i {0}; i < collList.Size(); ++i) {
            const std::string name {MadJson::getString(collList[i], "name")};
            const std::string art {MadJson::getString(collList[i], "art")};
            if (!name.empty() && !art.empty())
                mCollArt[name] = art;
        }
    }

    mCollOverrides.clear();
    const rapidjson::Value& collOverrides {MadJson::getMember(result, "collection_overrides")};
    if (collOverrides.IsObject()) {
        for (auto it = collOverrides.MemberBegin(); it != collOverrides.MemberEnd(); ++it) {
            std::vector<std::string> overrideNames;
            const rapidjson::Value& list {MadJson::getMember(it->value, "names")};
            if (list.IsArray()) {
                for (rapidjson::SizeType i {0}; i < list.Size(); ++i) {
                    if (list[i].IsString())
                        overrideNames.emplace_back(list[i].GetString());
                }
            }
            mCollOverrides.emplace_back(it->name.GetString(), joinNames(overrideNames));
        }
    }

    // ── layout ──
    // The whole content column scrolls as one (Tk _scroll parity): every
    // child lives inside mScroll at VIEW-LOCAL coordinates, full-height; the
    // overrides grid is no longer squeezed into the leftover viewport.
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    const float miniHeight {Font::get(FONT_SIZE_MINI)->getHeight()};

    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};

    mGlobalHeader = std::make_shared<TextComponent>("Global default", Font::get(FONT_SIZE_SMALL),
                                                    MadTheme::color(MadColor::Title), ALIGN_LEFT, ALIGN_CENTER,
                                                    glm::ivec2 {0, 0});
    mGlobalHeader->setPosition(0.0f, y);
    mGlobalHeader->setSize(mViewportSize.x, smallHeight);
    mScroll->addChild(mGlobalHeader.get());
    y += smallHeight;

    // Medium, matching the halved page titles.
    const float comboHeight {Font::get(FONT_SIZE_MEDIUM)->getHeight()};
    mComboLine = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_MEDIUM),
                                                 MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
                                                 glm::ivec2 {0, 0});
    mComboLine->setPosition(0.0f, y);
    mComboLine->setSize(mViewportSize.x, comboHeight);
    mScroll->addChild(mComboLine.get());
    y += comboHeight + smallHeight * 0.2f;

    // The hold-time stepper and the DETECT/SAVE buttons share one line (the
    // navigation is unchanged: down from the stepper reaches the buttons).
    mStepper = std::make_shared<MadStepper>(
        "hold time (s)", 0.3f, 3.0f, 0.1f, [](const float value) { return formatHold(value); },
        [this](const float value) {
            mHold = value;
            refreshComboLine();
            mPanel->refreshHelpPrompts(); // x=save appears once hold diverges from baseline
        });
    mStepper->setPosition(0.0f, y);
    mStepper->setSize(mViewportSize.x * 0.42f, Font::get(FONT_SIZE_MEDIUM)->getHeight() * 1.4f);
    mStepper->setValue(mHold);
    mScroll->addChild(mStepper.get());

    mDetectButton =
        std::make_shared<ButtonComponent>("DETECT", "detect", [this] { detectGlobal(); });
    const float buttonY {y + (mStepper->getSize().y - mDetectButton->getSize().y) / 2.0f};
    mDetectButton->setPosition(mStepper->getSize().x + mViewportSize.x * 0.03f, buttonY);
    mScroll->addChild(mDetectButton.get());
    mSaveButton = std::make_shared<ButtonComponent>("SAVE", "save", [this] { saveGlobal(); });
    mSaveButton->setPosition(mDetectButton->getPosition().x + mDetectButton->getSize().x +
                                 mViewportSize.x * 0.012f,
                             buttonY);
    mScroll->addChild(mSaveButton.get());
    y += mStepper->getSize().y + smallHeight * 0.5f;

    mPerSystemHeader = std::make_shared<TextComponent>(
        "Per system (overrides the global)", Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Title),
        ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    mPerSystemHeader->setPosition(0.0f, y);
    mPerSystemHeader->setSize(mViewportSize.x, smallHeight);
    mScroll->addChild(mPerSystemHeader.get());
    y += smallHeight;

    mWiiNote = std::make_shared<TextComponent>(
        "wii: + & −  (real Wii Remotes via DolphinBar — HID, fixed)", Font::get(FONT_SIZE_MINI),
        MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    mWiiNote->setPosition(0.0f, y);
    mWiiNote->setSize(mViewportSize.x, miniHeight);
    mScroll->addChild(mWiiNote.get());
    y += miniHeight + smallHeight * 0.3f;

    mAddButton = std::make_shared<ButtonComponent>(
        "ADD PER-SYSTEM COMBO", "add per-system combo",
        [this] { mPanel->pushPage(new GuiMadPageQuitComboPicker(mPanel)); });
    mAddButton->setPosition(0.0f, y);
    mScroll->addChild(mAddButton.get());
    y += mAddButton->getSize().y + smallHeight * 0.3f;

    if (mOverrides.empty()) {
        mNoOverrides = std::make_shared<TextComponent>(
            "  (none — every system uses the global combo)", Font::get(FONT_SIZE_SMALL),
            MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
        mNoOverrides->setPosition(0.0f, y);
        mNoOverrides->setSize(mViewportSize.x, smallHeight);
        mScroll->addChild(mNoOverrides.get());
        y += smallHeight;
    }
    else {
        std::vector<MadTileGrid::Tile> tiles;
        for (const auto& entry : mOverrides) {
            MadTileGrid::Tile tile;
            tile.key = entry.first;
            tile.label = entry.first;
            tile.sublabel = entry.second;
            const auto art = mSystemArt.find(entry.first);
            if (art != mSystemArt.end())
                tile.artPath = art->second;
            tiles.emplace_back(tile);
        }
        mGrid = std::make_shared<MadTileGrid>();
        mGrid->setPosition(0.0f, y);
        // Two-pass sizing: columns need the real width, the full height needs
        // the tiles laid out. At full height the grid's internal scroll is a
        // clamped no-op — the page scrolls it through mScroll instead.
        mGrid->setSize(mViewportSize.x, 1.0f);
        mGrid->setTiles(tiles);
        mGrid->setSize(mViewportSize.x, std::max(1.0f, mGrid->contentHeight()));
        mGrid->setOnPick([this](const std::string& system) {
            std::string comboNames;
            for (const auto& entry : mOverrides) {
                if (entry.first == system)
                    comboNames = entry.second;
            }
            std::string artPath;
            const auto art = mSystemArt.find(system);
            if (art != mSystemArt.end())
                artPath = art->second;
            mPanel->pushPage(new GuiMadPageQuitComboDetail(mPanel, system, comboNames, artPath));
        });
        mGrid->setCursorIndex(mGridCookie);
        mScroll->addChild(mGrid.get());
        y += mGrid->getSize().y;
    }

    // ── per collection (overrides the system/per-game combo) ──
    y += smallHeight * 0.4f;
    mPerCollHeader = std::make_shared<TextComponent>(
        "Per collection (overrides system)", Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Title),
        ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    mPerCollHeader->setPosition(0.0f, y);
    mPerCollHeader->setSize(mViewportSize.x, smallHeight);
    mScroll->addChild(mPerCollHeader.get());
    y += smallHeight;

    mAddCollButton = std::make_shared<ButtonComponent>(
        "ADD PER-COLLECTION COMBO", "add per-collection combo",
        [this] { mPanel->pushPage(new GuiMadPageQuitComboPicker(mPanel, true)); });
    mAddCollButton->setPosition(0.0f, y);
    mScroll->addChild(mAddCollButton.get());
    y += mAddCollButton->getSize().y + smallHeight * 0.3f;

    if (mCollOverrides.empty()) {
        mNoCollOverrides = std::make_shared<TextComponent>(
            "  (none — collections follow each game's system/global combo)",
            Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER,
            glm::ivec2 {0, 0});
        mNoCollOverrides->setPosition(0.0f, y);
        mNoCollOverrides->setSize(mViewportSize.x, smallHeight);
        mScroll->addChild(mNoCollOverrides.get());
        y += smallHeight;
    }
    else {
        std::vector<MadTileGrid::Tile> tiles;
        for (const auto& entry : mCollOverrides) {
            MadTileGrid::Tile tile;
            tile.key = entry.first;
            tile.label = entry.first;
            tile.sublabel = entry.second; // combo names.
            const auto art = mCollArt.find(entry.first);
            if (art != mCollArt.end())
                tile.artPath = art->second;
            tiles.emplace_back(tile);
        }
        mCollGrid = std::make_shared<MadTileGrid>();
        mCollGrid->setPosition(0.0f, y);
        mCollGrid->setSize(mViewportSize.x, 1.0f);
        mCollGrid->setTiles(tiles);
        mCollGrid->setSize(mViewportSize.x, std::max(1.0f, mCollGrid->contentHeight()));
        mCollGrid->setOnPick([this](const std::string& name) {
            std::string comboNames;
            for (const auto& entry : mCollOverrides) {
                if (entry.first == name)
                    comboNames = entry.second;
            }
            std::string artPath;
            const auto art = mCollArt.find(name);
            if (art != mCollArt.end())
                artPath = art->second;
            // Display the bare collection name; write the "collection-<name>" scope.
            mPanel->pushPage(new GuiMadPageQuitComboDetail(mPanel, name, comboNames, artPath,
                                                           "collection-" + name));
        });
        mCollGrid->setCursorIndex(mCollGridCookie);
        mScroll->addChild(mCollGrid.get());
        y += mCollGrid->getSize().y;
    }

    mScroll->setContentHeight(y + smallHeight * 0.5f);
    mScroll->setScrollOffset(mScrollCookie); // Survives the per-child-pop rebuild.

    refreshComboLine();
    mBuilt = true;
    if (mFocusTarget == FocusGrid && mGrid == nullptr)
        mFocusTarget = FocusAdd;
    if (mFocusTarget == FocusGridColl && mCollGrid == nullptr)
        mFocusTarget = FocusAddColl;
    setFocusTarget(mFocusTarget);
    followFocus();
}

void GuiMadPageQuitCombo::refreshComboLine()
{
    // Just the combo: the hold time is already visible on the stepper beside it.
    if (mComboLine != nullptr)
        mComboLine->setText("  " + comboString());
}

void GuiMadPageQuitCombo::detectGlobal()
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiMadCaptureModal(
        mPanel, "combo", "Hold the combo, then release…",
        [this, alive](const GuiMadCaptureModal::Result* result) {
            if (alive.expired() || result == nullptr || result->held.empty())
                return;
            mComboButtons = result->held;
            mComboNames = result->names;
            refreshComboLine();
            mPanel->refreshHelpPrompts(); // x=save appears now the combo diverges from baseline
            footer()->setStatus("Captured " + std::to_string(result->held.size()) +
                                " button(s) — press X to save.");
        }));
}

void GuiMadPageQuitCombo::saveGlobal()
{
    const std::vector<int> buttons {mComboButtons};
    const float holdSec {mHold};
    pageRequest(
        "policy.set_quit_combo",
        [buttons, holdSec](MadJson::Writer& writer) {
            writer.Key("scope");
            writer.Null();
            writer.Key("buttons");
            writer.StartArray();
            for (const int button : buttons)
                writer.Int(button);
            writer.EndArray();
            writer.Key("hold_sec");
            writer.Double(static_cast<double>(holdSec));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't save the global combo: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            // Clear the "Captured … press X to save." sticky — it's resolved now.
            footer()->setStatus("");
            // Advance the baseline so dirty clears (the SAVE button and X=Save
            // both land here).
            mBaselineButtons = mComboButtons;
            mBaselineHold = mHold;
            mPanel->refreshHelpPrompts();
            footer()->flash("Saved global combo (" + comboString() + " · hold " +
                            formatHold(mHold) + "s)");
        });
}

bool GuiMadPageQuitCombo::hasUnsavedEdits() const
{
    // Hold-time is a float on a 0.1s grid; stepping up then back can leave a 1-ULP
    // residue vs the loaded baseline, so compare with a half-step tolerance (the
    // button list is an exact integer-vector compare).
    const float dh {mHold - mBaselineHold};
    return mComboButtons != mBaselineButtons || dh > 0.05f || dh < -0.05f;
}

bool GuiMadPageQuitCombo::madSave()
{
    if (!hasUnsavedEdits())
        return false;
    saveGlobal(); // baseline advances in the success callback
    return true;
}

bool GuiMadPageQuitCombo::madCancel()
{
    if (!hasUnsavedEdits())
        return false;
    refreshData(false); // re-read the global combo from disk = discard the staged edit
    return true;
}

void GuiMadPageQuitCombo::setFocusTarget(const int target)
{
    mFocusTarget = target;
    if (mStepper != nullptr) {
        if (target == FocusStepper)
            mStepper->onFocusGained();
        else
            mStepper->onFocusLost();
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
    applyButton(mDetectButton, FocusDetect);
    applyButton(mSaveButton, FocusSave);
    applyButton(mAddButton, FocusAdd);
    applyButton(mAddCollButton, FocusAddColl);
    if (mGrid != nullptr) {
        if (target == FocusGrid)
            mGrid->onFocusGained();
        else
            mGrid->onFocusLost();
    }
    if (mCollGrid != nullptr) {
        if (target == FocusGridColl)
            mCollGrid->onFocusGained();
        else
            mCollGrid->onFocusLost();
    }
    mPanel->refreshHelpPrompts();
}

void GuiMadPageQuitCombo::moveFocus(const int target)
{
    setFocusTarget(target);
    followFocus();
}

void GuiMadPageQuitCombo::followFocus()
{
    if (mScroll == nullptr)
        return;
    float top {0.0f};
    float bottom {0.0f};
    switch (mFocusTarget) {
        case FocusStepper: {
            // Topmost focusable: reveal the intro/header context above it too.
            top = 0.0f;
            bottom = mStepper->getPosition().y + mStepper->getSize().y;
            break;
        }
        case FocusDetect:
        case FocusSave: {
            top = mDetectButton->getPosition().y;
            bottom = top + mDetectButton->getSize().y;
            break;
        }
        case FocusAdd: {
            top = mAddButton->getPosition().y;
            bottom = top + mAddButton->getSize().y;
            break;
        }
        case FocusGrid: {
            if (mGrid == nullptr)
                return;
            const glm::vec2 row {mGrid->cursorRowRect()};
            top = mGrid->getPosition().y + row.x;
            bottom = mGrid->getPosition().y + row.y;
            break;
        }
        case FocusAddColl: {
            top = mAddCollButton->getPosition().y;
            bottom = top + mAddCollButton->getSize().y;
            break;
        }
        case FocusGridColl: {
            if (mCollGrid == nullptr)
                return;
            const glm::vec2 row {mCollGrid->cursorRowRect()};
            top = mCollGrid->getPosition().y + row.x;
            bottom = mCollGrid->getPosition().y + row.y;
            break;
        }
        default:
            return;
    }
    mScroll->ensureVisible(top, bottom);
}

std::vector<MadPage::PagedTarget> GuiMadPageQuitCombo::pagedTargets() const
{
    // Layout order == top order (pickPagedTarget relies on it). The DETECT/
    // SAVE pair shares a row; one entry stands in for it.
    std::vector<PagedTarget> targets;
    targets.push_back({FocusStepper, -1, mStepper->getPosition().y,
                       mStepper->getPosition().y + mStepper->getSize().y});
    targets.push_back({FocusDetect, -1, mDetectButton->getPosition().y,
                       mDetectButton->getPosition().y + mDetectButton->getSize().y});
    targets.push_back({FocusAdd, -1, mAddButton->getPosition().y,
                       mAddButton->getPosition().y + mAddButton->getSize().y});
    if (mGrid != nullptr) {
        for (int row {0}; row < mGrid->rows(); ++row) {
            const glm::vec2 rect {mGrid->rowRect(row)};
            targets.push_back({FocusGrid, row, mGrid->getPosition().y + rect.x,
                               mGrid->getPosition().y + rect.y});
        }
    }
    targets.push_back({FocusAddColl, -1, mAddCollButton->getPosition().y,
                       mAddCollButton->getPosition().y + mAddCollButton->getSize().y});
    if (mCollGrid != nullptr) {
        for (int row {0}; row < mCollGrid->rows(); ++row) {
            const glm::vec2 rect {mCollGrid->rowRect(row)};
            targets.push_back({FocusGridColl, row, mCollGrid->getPosition().y + rect.x,
                               mCollGrid->getPosition().y + rect.y});
        }
    }
    return targets;
}

void GuiMadPageQuitCombo::applyPagedTarget(const PagedTarget& target)
{
    if (target.id == FocusGrid && mGrid != nullptr) {
        // Land on the picked row, keeping the cursor's column (silent move —
        // per-step sounds would machine-gun on a page jump).
        const int columns {std::max(1, mGrid->columns())};
        const int column {mGrid->cursorIndex() % columns};
        mGrid->setCursorIndex(
            std::min(target.aux * columns + column, mGrid->tileCount() - 1));
    }
    else if (target.id == FocusGridColl && mCollGrid != nullptr) {
        const int columns {std::max(1, mCollGrid->columns())};
        const int column {mCollGrid->cursorIndex() % columns};
        mCollGrid->setCursorIndex(
            std::min(target.aux * columns + column, mCollGrid->tileCount() - 1));
    }
    setFocusTarget(target.id);
}

bool GuiMadPageQuitCombo::input(InputConfig* config, Input input)
{
    if (!mBuilt)
        return false;

    if (mFocusTarget == FocusStepper) {
        if (mStepper->input(config, input))
            return true;
        if (input.value != 0 && config->isMappedLike("down", input)) {
            moveFocus(FocusDetect);
            return true;
        }
        if (input.value != 0 && config->isMappedLike("up", input))
            return true; // Top edge: nothing above.
        return false;
    }

    if (mFocusTarget == FocusDetect || mFocusTarget == FocusSave) {
        if (input.value == 0)
            return false;
        if (config->isMappedLike("left", input)) {
            if (mFocusTarget == FocusSave) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                setFocusTarget(FocusDetect);
            }
            return true;
        }
        if (config->isMappedLike("right", input)) {
            if (mFocusTarget == FocusDetect) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                setFocusTarget(FocusSave);
            }
            return true;
        }
        if (config->isMappedLike("up", input)) {
            moveFocus(FocusStepper);
            return true;
        }
        if (config->isMappedLike("down", input)) {
            moveFocus(FocusAdd);
            return true;
        }
        if (config->isMappedTo("a", input)) {
            return mFocusTarget == FocusDetect ? mDetectButton->input(config, input) :
                                                 mSaveButton->input(config, input);
        }
        return false;
    }

    if (mFocusTarget == FocusAdd) {
        if (input.value == 0)
            return false;
        if (config->isMappedLike("up", input)) {
            moveFocus(FocusDetect);
            return true;
        }
        if (config->isMappedLike("down", input)) {
            // Skip an absent per-system grid straight to the collection section.
            moveFocus(mGrid != nullptr ? FocusGrid : FocusAddColl);
            return true;
        }
        if (config->isMappedTo("a", input))
            return mAddButton->input(config, input);
        return false;
    }

    if (mFocusTarget == FocusGrid) {
        if (mGrid == nullptr) {
            moveFocus(FocusAdd);
            return true;
        }
        // The grid CAROUSEL-WRAPS up/down within a column, so "cursor didn't move" can't
        // detect an edge on a multi-row grid. Detect it the way the grid itself does — no
        // tile above (top row) / no tile below (handles a SHORT last row per column) — and
        // escape without forwarding the keypress (which would wrap): UP to the ADD button,
        // DOWN into the collection section.
        const int cols {std::max(1, mGrid->columns())};
        if (input.value != 0 && config->isMappedLike("up", input)) {
            if (mGrid->cursorIndex() < cols)
                moveFocus(FocusAdd);
            else {
                mGrid->input(config, input);
                followFocus();
            }
            return true;
        }
        if (input.value != 0 && config->isMappedLike("down", input)) {
            if (mGrid->cursorIndex() + cols >= mGrid->tileCount())
                moveFocus(FocusAddColl);
            else {
                mGrid->input(config, input);
                followFocus();
            }
            return true;
        }
        if (mGrid->input(config, input)) {
            followFocus(); // left/right within the row.
            return true;
        }
        return false;
    }

    if (mFocusTarget == FocusAddColl) {
        if (input.value == 0)
            return false;
        if (config->isMappedLike("up", input)) {
            moveFocus(mGrid != nullptr ? FocusGrid : FocusAdd);
            return true;
        }
        if (config->isMappedLike("down", input)) {
            if (mCollGrid != nullptr)
                moveFocus(FocusGridColl);
            return true;
        }
        if (config->isMappedTo("a", input))
            return mAddCollButton->input(config, input);
        return false;
    }

    // FocusGridColl.
    if (mCollGrid == nullptr) {
        moveFocus(FocusAddColl);
        return true;
    }
    // Same carousel-wrap-safe edge detection as the per-system grid. This is the last
    // section, so DOWN with no tile below just stays put (no wrap, nothing below).
    const int collCols {std::max(1, mCollGrid->columns())};
    if (input.value != 0 && config->isMappedLike("up", input)) {
        if (mCollGrid->cursorIndex() < collCols)
            moveFocus(FocusAddColl);
        else {
            mCollGrid->input(config, input);
            followFocus();
        }
        return true;
    }
    if (input.value != 0 && config->isMappedLike("down", input)) {
        if (mCollGrid->cursorIndex() + collCols < mCollGrid->tileCount()) {
            mCollGrid->input(config, input);
            followFocus();
        }
        return true; // no tile below → stay (don't wrap)
    }
    if (mCollGrid->input(config, input)) {
        followFocus(); // left/right within the row.
        return true;
    }
    return false;
}

void GuiMadPageQuitCombo::pageScroll(int direction)
{
    if (!mBuilt || mScroll == nullptr)
        return;
    // Tk _scroll parity: page the VIEW, then land focus on the lowest (RT) /
    // highest (LT) control whose top edge is inside the new window; if none
    // qualifies, focus stays and so does the view. With nothing to scroll the
    // pick runs over the whole content (a plain first/last focus jump).
    const std::vector<PagedTarget> targets {pagedTargets()};
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
        if (!changed && target.id == FocusGrid && mGrid != nullptr)
            changed = target.aux != mGrid->cursorIndex() / std::max(1, mGrid->columns());
        applyPagedTarget(target);
        followFocus(); // Reveal the landed control fully (Tk's ensure-visible).
        if (changed)
            moved = true;
    }
    // Silent when nothing happened (repeated RT at the bottom must not click).
    if (moved)
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
}

std::vector<HelpPrompt> GuiMadPageQuitCombo::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (!mBuilt)
        return prompts;
    if (mFocusTarget == FocusGrid && mGrid != nullptr)
        prompts = mGrid->getHelpPrompts();
    else if (mFocusTarget == FocusGridColl && mCollGrid != nullptr)
        prompts = mCollGrid->getHelpPrompts();
    else {
        prompts.push_back(HelpPrompt("up/down", "choose"));
        if (mFocusTarget == FocusStepper)
            prompts.push_back(HelpPrompt("left/right", "adjust"));
        else
            prompts.push_back(HelpPrompt("a", "select"));
    }
    if (hasUnsavedEdits()) {
        prompts.push_back(HelpPrompt("x", "save"));
        prompts.push_back(HelpPrompt("y", "cancel"));
    }
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void GuiMadPageQuitCombo::onSaveFocus()
{
    mFocusCookie = mFocusTarget;
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
    if (mCollGrid != nullptr)
        mCollGridCookie = mCollGrid->cursorIndex();
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void GuiMadPageQuitCombo::onRestoreFocus()
{
    if (!mBuilt)
        return;
    setFocusTarget(mFocusCookie);
    if (mFocusTarget == FocusGrid && mGrid != nullptr)
        mGrid->setCursorIndex(mGridCookie);
    if (mFocusTarget == FocusGridColl && mCollGrid != nullptr)
        mCollGrid->setCursorIndex(mCollGridCookie);
    if (mScroll != nullptr)
        mScroll->setScrollOffset(mScrollCookie);
    followFocus();
}

//  ── GuiMadPageQuitComboPicker ──

GuiMadPageQuitComboPicker::GuiMadPageQuitComboPicker(GuiMadPanel* panel, bool collections)
    : MadPage {panel,
               collections ? "ADD PER-COLLECTION QUIT COMBO" : "ADD PER-SYSTEM QUIT COMBO"}
    , mCollections {collections}
{
}

void GuiMadPageQuitComboPicker::build()
{
    mIntro = std::make_shared<TextComponent>(
        mCollections
            ? "Pick a collection, then hold the combo you want (~1s, then release)."
            : "Pick a system, then hold the combo you want (~1s, then release).",
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(mViewportPos.x, mViewportPos.y);
    mIntro->setSize(mViewportSize.x, 0.0f);
    addChild(mIntro.get());

    setLoadingText(mCollections ? "Loading collections…" : "Loading systems…");
    pageRequest(
        "systems.list", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            std::map<std::string, std::string> art;
            if (ok) {
                const rapidjson::Value& systems {MadJson::getMember(payload, "systems")};
                if (systems.IsArray()) {
                    for (rapidjson::SizeType i {0}; i < systems.Size(); ++i) {
                        art[MadJson::getString(systems[i], "name")] =
                            MadJson::getString(systems[i], "art");
                    }
                }
            }
            pageRequest("quitcombo.get", nullptr,
                        [this, art](bool ok, const rapidjson::Value& payload) {
                            setLoadingText("");
                            if (!ok) {
                                footer()->setStatus(
                                    "Couldn't load the quit combos: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                                return;
                            }
                            std::vector<MadTileGrid::Tile> tiles;
                            if (mCollections) {
                                // Enabled collections minus those already carrying a combo.
                                // Collections have no console art — label + game-count only.
                                const rapidjson::Value& overrides {
                                    MadJson::getMember(payload, "collection_overrides")};
                                const rapidjson::Value& collections {
                                    MadJson::getMember(payload, "collections")};
                                if (collections.IsArray()) {
                                    for (rapidjson::SizeType i {0}; i < collections.Size(); ++i) {
                                        const std::string name {
                                            MadJson::getString(collections[i], "name")};
                                        if (name.empty())
                                            continue;
                                        if (overrides.IsObject() &&
                                            overrides.HasMember(name.c_str()))
                                            continue;
                                        MadTileGrid::Tile tile;
                                        tile.key = name;
                                        tile.label = name;
                                        const int count {
                                            MadJson::getInt(collections[i], "count", 0)};
                                        if (count > 0)
                                            tile.sublabel = std::to_string(count) + " games";
                                        const std::string art {
                                            MadJson::getString(collections[i], "art")};
                                        if (!art.empty())
                                            tile.artPath = art;
                                        tiles.emplace_back(tile);
                                    }
                                }
                            }
                            else {
                                // Eligible systems minus already-overridden, with console art.
                                const rapidjson::Value& overrides {
                                    MadJson::getMember(payload, "overrides")};
                                const rapidjson::Value& eligible {
                                    MadJson::getMember(payload, "eligible")};
                                if (eligible.IsArray()) {
                                    for (rapidjson::SizeType i {0}; i < eligible.Size(); ++i) {
                                        if (!eligible[i].IsString())
                                            continue;
                                        const std::string system {eligible[i].GetString()};
                                        if (overrides.IsObject() &&
                                            overrides.HasMember(system.c_str()))
                                            continue;
                                        MadTileGrid::Tile tile;
                                        tile.key = system;
                                        tile.label = system;
                                        const auto it = art.find(system);
                                        if (it != art.end())
                                            tile.artPath = it->second;
                                        tiles.emplace_back(tile);
                                    }
                                }
                            }

                            if (tiles.empty()) {
                                setLoadingText(
                                    mCollections
                                        ? "Every collection already has a combo."
                                        : "All eligible systems already have an override.");
                                return;
                            }

                            const float top {mIntro->getPosition().y + mIntro->getSize().y +
                                             Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f};
                            mGrid = std::make_shared<MadTileGrid>();
                            mGrid->setPosition(mViewportPos.x, top);
                            mGrid->setSize(mViewportSize.x,
                                           mViewportPos.y + mViewportSize.y - top);
                            mGrid->setTiles(tiles);
                            mGrid->setOnPick(
                                [this](const std::string& system) { armCapture(system); });
                            mGrid->setCursorIndex(mFocusCookie);
                            mGrid->onFocusGained(); // Only focusable here.
                            addChild(mGrid.get());
                            mPanel->refreshHelpPrompts();
                        });
        },
        10000);
}

void GuiMadPageQuitComboPicker::armCapture(const std::string& label)
{
    std::weak_ptr<int> alive {pageAlive()};
    // Systems store under their own name; collections under "collection-<name>" so the
    // scope matches what the quit-combo-watcher hook looks up.
    const std::string scope {mCollections ? "collection-" + label : label};
    mWindow->pushGui(new GuiMadCaptureModal(
        mPanel, "combo", "Hold the combo for " + label + ", then release…",
        [this, alive, label, scope](const GuiMadCaptureModal::Result* result) {
            if (alive.expired() || result == nullptr || result->held.empty())
                return;
            const std::vector<int> buttons {result->held};
            pageRequest(
                "policy.set_quit_combo",
                [scope, buttons](MadJson::Writer& writer) {
                    writer.Key("scope");
                    writer.String(scope.c_str(),
                                  static_cast<rapidjson::SizeType>(scope.length()));
                    writer.Key("buttons");
                    writer.StartArray();
                    for (const int button : buttons)
                        writer.Int(button);
                    writer.EndArray();
                },
                [this, label](bool ok, const rapidjson::Value& payload) {
                    if (!ok) {
                        footer()->flash(
                            "Couldn't save the " + label + " combo: " +
                                MadJson::getString(payload, "message", "unknown error"),
                            4000, true);
                        return;
                    }
                    footer()->flash("Saved " + label + " combo");
                    mPanel->popPage(); // Back to the root page (it rebuilds).
                });
        }));
}

bool GuiMadPageQuitComboPicker::input(InputConfig* config, Input input)
{
    if (mGrid != nullptr)
        return mGrid->input(config, input);
    return false;
}

void GuiMadPageQuitComboPicker::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageQuitComboPicker::getHelpPrompts()
{
    if (mGrid != nullptr)
        return mGrid->getHelpPrompts();
    return std::vector<HelpPrompt>();
}

void GuiMadPageQuitComboPicker::onSaveFocus()
{
    if (mGrid != nullptr)
        mFocusCookie = mGrid->cursorIndex();
}

void GuiMadPageQuitComboPicker::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mFocusCookie);
}

//  ── GuiMadPageQuitComboDetail ──

GuiMadPageQuitComboDetail::GuiMadPageQuitComboDetail(GuiMadPanel* panel,
                                                     const std::string& system,
                                                     const std::string& comboNames,
                                                     const std::string& artPath,
                                                     const std::string& scopeKey)
    : MadPage {panel, "QUIT COMBO: " + Utils::String::toUpper(system)}
    , mSystem {system}
    , mScopeKey {scopeKey.empty() ? system : scopeKey}
    , mComboNames {comboNames}
    , mArtPath {artPath}
    , mButtonFocus {0}
{
}

void GuiMadPageQuitComboDetail::build()
{
    float y {mViewportPos.y};

    if (!mArtPath.empty()) {
        const float artHeight {mViewportSize.y * 0.24f};
        mArt = std::make_shared<ImageComponent>();
        mArt->setOrigin(0.5f, 0.5f);
        mArt->setMaxSize(mViewportSize.x * 0.4f, artHeight);
        mArt->setImage(mArtPath);
        mArt->setPosition(mViewportPos.x + mViewportSize.x / 2.0f, y + artHeight / 2.0f);
        addChild(mArt.get());
        y += artHeight + mViewportSize.y * 0.03f;
    }

    // Medium, matching the halved page titles.
    const float comboHeight {Font::get(FONT_SIZE_MEDIUM)->getHeight()};
    mComboLine = std::make_shared<TextComponent>("Override combo:  " + mComboNames,
                                                 Font::get(FONT_SIZE_MEDIUM), MadTheme::color(MadColor::Primary),
                                                 ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    mComboLine->setPosition(mViewportPos.x, y);
    mComboLine->setSize(mViewportSize.x, comboHeight);
    addChild(mComboLine.get());
    y += comboHeight + Font::get(FONT_SIZE_SMALL)->getHeight() * 0.6f;

    mRedetectButton =
        std::make_shared<ButtonComponent>("RE-DETECT", "re-detect", [this] { redetect(); });
    mRedetectButton->setPosition(mViewportPos.x, y);
    addChild(mRedetectButton.get());

    mClearButton = std::make_shared<ButtonComponent>("CLEAR OVERRIDE", "clear override",
                                                     [this] { clearOverride(); });
    mClearButton->setPosition(mViewportPos.x + mRedetectButton->getSize().x +
                                  mViewportSize.x * 0.012f,
                              y);
    addChild(mClearButton.get());

    applyButtonFocus();
}

void GuiMadPageQuitComboDetail::applyButtonFocus()
{
    if (mButtonFocus == 0) {
        mRedetectButton->onFocusGained();
        mClearButton->onFocusLost();
    }
    else {
        mRedetectButton->onFocusLost();
        mClearButton->onFocusGained();
    }
}

void GuiMadPageQuitComboDetail::redetect()
{
    std::weak_ptr<int> alive {pageAlive()};
    const std::string system {mSystem};
    const std::string scope {mScopeKey};
    mWindow->pushGui(new GuiMadCaptureModal(
        mPanel, "combo", "Hold the combo for " + system + ", then release…",
        [this, alive, system, scope](const GuiMadCaptureModal::Result* result) {
            if (alive.expired() || result == nullptr || result->held.empty())
                return;
            const std::vector<int> buttons {result->held};
            const std::string comboNames {joinNames(result->names)};
            pageRequest(
                "policy.set_quit_combo",
                [scope, buttons](MadJson::Writer& writer) {
                    writer.Key("scope");
                    writer.String(scope.c_str(),
                                  static_cast<rapidjson::SizeType>(scope.length()));
                    writer.Key("buttons");
                    writer.StartArray();
                    for (const int button : buttons)
                        writer.Int(button);
                    writer.EndArray();
                },
                [this, system, comboNames](bool ok, const rapidjson::Value& payload) {
                    if (!ok) {
                        footer()->flash(
                            "Couldn't save the " + system + " combo: " +
                                MadJson::getString(payload, "message", "unknown error"),
                            4000, true);
                        return;
                    }
                    mComboNames = comboNames;
                    mComboLine->setText("Override combo:  " + mComboNames);
                    footer()->flash("Saved " + system + " combo");
                });
        }));
}

void GuiMadPageQuitComboDetail::clearOverride()
{
    const std::string system {mSystem};
    const std::string scope {mScopeKey};
    const bool isColl {scope != system};
    pageRequest(
        "policy.clear_quit_combo",
        [scope](MadJson::Writer& writer) {
            writer.Key("system");
            writer.String(scope.c_str(), static_cast<rapidjson::SizeType>(scope.length()));
        },
        [this, system, isColl](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't clear the " + system + " override: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            footer()->flash("Override cleared — " + system +
                            (isColl ? " uses the system/global combo" : " uses the global combo"));
            mPanel->popPage(); // 'this' dies here; nothing below touches members.
        });
}

bool GuiMadPageQuitComboDetail::input(InputConfig* config, Input input)
{
    if (input.value == 0)
        return false;

    if (config->isMappedLike("left", input)) {
        if (mButtonFocus == 1) {
            mButtonFocus = 0;
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            applyButtonFocus();
        }
        return true;
    }
    if (config->isMappedLike("right", input)) {
        if (mButtonFocus == 0) {
            mButtonFocus = 1;
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            applyButtonFocus();
        }
        return true;
    }
    if (config->isMappedTo("a", input)) {
        return mButtonFocus == 0 ? mRedetectButton->input(config, input) :
                                   mClearButton->input(config, input);
    }
    return false;
}

std::vector<HelpPrompt> GuiMadPageQuitComboDetail::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    prompts.push_back(HelpPrompt("left/right", "choose"));
    prompts.push_back(HelpPrompt("a", "select"));
    return prompts;
}
