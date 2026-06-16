//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadPlayerSlots.cpp
//
//  8-slot player-pin editor for the MAD control panel (deck-patches).
//

#include "guis/mad/widgets/MadPlayerSlots.h"

#include "Sound.h"

#include <cmath>
#include "guis/mad/MadTheme.h"

MadPlayerSlots::MadPlayerSlots()
    : mRenderer {Renderer::getInstance()}
    , mFocused {false}
    , mFocusRow {0}
    , mFocusCol {0}
    , mColumns {1}
    , mCellWidth {0.0f}
    , mSaveHeight {0.0f}
    , mRowHeight {0.0f}
    , mScrollOffset {0.0f}
{
    // All components are rendered manually (clipped + scrolled), not children.
    mSaveButton = std::make_shared<ButtonComponent>("SAVE PINS", "save pins", [this] {
        if (mOnSave)
            mOnSave(mPins);
    });

    for (int player {1}; player <= PLAYER_COUNT; ++player) {
        Row row;
        row.title = std::make_shared<TextComponent>(
            "Player " + std::to_string(player), Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Title),
            ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
        row.description = std::make_shared<TextComponent>(
            "  (unpinned)", Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary), ALIGN_LEFT,
            ALIGN_CENTER, glm::ivec2 {0, 0});
        row.identify = std::make_shared<ButtonComponent>("IDENTIFY", "identify",
                                                         [this, player] {
                                                             if (mOnIdentify)
                                                                 mOnIdentify(player);
                                                         });
        row.clear = std::make_shared<ButtonComponent>("CLEAR", "clear", [this, player] {
            mPins.erase(player);
            refreshDescriptions();
            if (mOnClear)
                mOnClear(player);
        });
        mRows.emplace_back(row);
    }

    applyFocus();
}

void MadPlayerSlots::onSizeChanged()
{
    layout();
}

void MadPlayerSlots::layout()
{
    if (mSize.x <= 0.0f || mSize.y <= 0.0f)
        return;

    const float lineHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    const float buttonHeight {mSaveButton->getSize().y};
    const float pad {lineHeight * 0.35f};

    // Player cells flow into columns so the wide screen is used; a cell must
    // hold its two buttons plus breathing room.
    const float minCellWidth {
        mRows.empty() ? mSize.x :
                        (mRows[0].identify->getSize().x + mRows[0].clear->getSize().x +
                         pad * 4.0f) *
                            1.25f};
    mColumns = std::max(1, static_cast<int>(mSize.x / std::max(1.0f, minCellWidth)));
    mColumns = std::min(mColumns, PLAYER_COUNT);
    mCellWidth = mSize.x / static_cast<float>(mColumns);

    mSaveHeight = buttonHeight + pad * 2.0f;
    mRowHeight = lineHeight * 2.0f + buttonHeight + pad * 2.0f;

    mSaveButton->setPosition(0.0f, pad);

    for (size_t i {0}; i < mRows.size(); ++i) {
        const float cellX {static_cast<float>(static_cast<int>(i) % mColumns) * mCellWidth};
        const float rowTop {mSaveHeight +
                            static_cast<float>(static_cast<int>(i) / mColumns) * mRowHeight};
        Row& row {mRows[i]};
        row.title->setPosition(cellX, rowTop + pad);
        row.title->setSize(mCellWidth - pad, lineHeight);
        row.description->setPosition(cellX, rowTop + pad + lineHeight);
        row.description->setSize(mCellWidth - pad, lineHeight);
        row.identify->setPosition(cellX, rowTop + pad + lineHeight * 2.0f);
        row.clear->setPosition(cellX + row.identify->getSize().x + pad,
                               rowTop + pad + lineHeight * 2.0f);
    }

    keepRowVisible();
}

void MadPlayerSlots::setPins(const std::map<int, std::string>& pins)
{
    mPins = pins;
    refreshDescriptions();
}

void MadPlayerSlots::setDevices(const std::vector<Device>& devices)
{
    mDevices = devices;
    refreshDescriptions();
}

void MadPlayerSlots::assignPin(const int player, const std::string& pinId,
                               const std::string& name)
{
    if (player < 1 || player > PLAYER_COUNT || pinId.empty())
        return;
    for (auto it = mPins.begin(); it != mPins.end();) {
        if (it->second == pinId && it->first != player)
            it = mPins.erase(it);
        else
            ++it;
    }
    mPins[player] = pinId;
    if (!name.empty()) {
        bool known {false};
        for (const Device& device : mDevices) {
            if (device.pinId == pinId)
                known = true;
        }
        if (!known)
            mDevices.emplace_back(Device {name, pinId});
    }
    refreshDescriptions();
}

std::string MadPlayerSlots::describePin(const std::string& pinId, std::string& badge) const
{
    // Badge from the pin_id prefix: uniq:/port:/vidpid: (lib.devices.pin_id).
    if (pinId.rfind("uniq:", 0) == 0)
        badge = "✓ MAC";
    else if (pinId.rfind("port:", 0) == 0)
        badge = "⚠ USB-port";
    else if (pinId.rfind("vidpid:", 0) == 0)
        badge = "⚠ model-only";
    else
        badge = pinId.substr(0, pinId.find(':'));

    for (const Device& device : mDevices) {
        if (device.pinId == pinId)
            return device.name;
    }
    return "(not connected)";
}

void MadPlayerSlots::refreshDescriptions()
{
    for (size_t i {0}; i < mRows.size(); ++i) {
        const auto it = mPins.find(static_cast<int>(i) + 1);
        if (it == mPins.end() || it->second.empty()) {
            mRows[i].description->setText("  (unpinned)");
            mRows[i].description->setColor(MadTheme::color(MadColor::Secondary));
            continue;
        }
        std::string badge;
        const std::string name {describePin(it->second, badge)};
        mRows[i].description->setText("  " + name + " · " + badge);
        // ✓ MAC pins are port-agnostic (accent); everything else is a warning.
        mRows[i].description->setColor(it->second.rfind("uniq:", 0) == 0 ? MadTheme::color(MadColor::Green) :
                                                                           MadTheme::color(MadColor::Red));
    }
}

ButtonComponent* MadPlayerSlots::focusedButton()
{
    if (mFocusRow == 0)
        return mSaveButton.get();
    Row& row {mRows[mFocusRow - 1]};
    return mFocusCol == 0 ? row.identify.get() : row.clear.get();
}

void MadPlayerSlots::applyFocus()
{
    mSaveButton->onFocusLost();
    for (Row& row : mRows) {
        row.identify->onFocusLost();
        row.clear->onFocusLost();
    }
    if (mFocused)
        focusedButton()->onFocusGained();
}

void MadPlayerSlots::keepRowVisible()
{
    if (mRowHeight <= 0.0f || mSize.y <= 0.0f)
        return;

    const glm::vec2 rect {rowRect(mFocusRow)};
    if (rect.x < mScrollOffset)
        mScrollOffset = rect.x;
    else if (rect.y > mScrollOffset + mSize.y)
        mScrollOffset = rect.y - mSize.y;

    mScrollOffset = glm::clamp(mScrollOffset, 0.0f,
                               std::max(0.0f, contentHeight() - mSize.y));
}

void MadPlayerSlots::onFocusGained()
{
    mFocused = true;
    applyFocus();
}

void MadPlayerSlots::onFocusLost()
{
    mFocused = false;
    applyFocus();
}

void MadPlayerSlots::setFocusCookie(const int cookie)
{
    mFocusRow = glm::clamp(cookie, 0, PLAYER_COUNT);
    mFocusCol = 0;
    applyFocus();
    keepRowVisible();
}

void MadPlayerSlots::focusFirstRow()
{
    setFocusCookie(0);
}

void MadPlayerSlots::focusLastRow()
{
    setFocusCookie(PLAYER_COUNT);
}

bool MadPlayerSlots::input(InputConfig* config, Input input)
{
    if (input.value == 0)
        return false;

    const int columns {std::max(1, mColumns)};
    if (config->isMappedLike("up", input)) {
        if (mFocusRow == 0)
            return false; // Edge: the page moves focus to whatever sits above.
        const int target {mFocusRow - columns};
        mFocusRow = target < 1 ? 0 : target; // Above the first grid line: SAVE.
        mFocusCol = 0;
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        applyFocus();
        keepRowVisible();
        return true;
    }
    if (config->isMappedLike("down", input)) {
        const int target {mFocusRow == 0 ? 1 : mFocusRow + columns};
        if (target > PLAYER_COUNT)
            return false; // Edge: the page moves focus to whatever sits below.
        mFocusRow = target;
        mFocusCol = 0;
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        applyFocus();
        keepRowVisible();
        return true;
    }
    if (config->isMappedLike("left", input)) {
        if (mFocusRow == 0)
            return true;
        if (mFocusCol == 1) {
            mFocusCol = 0; // CLEAR → IDENTIFY within the cell.
        }
        else if ((mFocusRow - 1) % columns != 0) {
            --mFocusRow; // Leftward into the neighbour cell's CLEAR.
            mFocusCol = 1;
        }
        else {
            return true; // Leftmost column: stay.
        }
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        applyFocus();
        return true;
    }
    if (config->isMappedLike("right", input)) {
        if (mFocusRow == 0)
            return true;
        if (mFocusCol == 0) {
            mFocusCol = 1; // IDENTIFY → CLEAR within the cell.
        }
        else if ((mFocusRow - 1) % columns != columns - 1 && mFocusRow < PLAYER_COUNT) {
            ++mFocusRow; // Rightward into the neighbour cell's IDENTIFY.
            mFocusCol = 0;
        }
        else {
            return true; // Rightmost column / last player: stay.
        }
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        applyFocus();
        return true;
    }
    if (config->isMappedTo("a", input))
        return focusedButton()->input(config, input);

    return false;
}

void MadPlayerSlots::render(const glm::mat4& parentTrans)
{
    if (!isVisible())
        return;

    glm::mat4 trans {parentTrans * getTransform()};

    // Clip to the widget (same scheme as MadTileGrid: scale-aware dimensions).
    glm::vec3 dim {mSize.x, mSize.y, 0.0f};
    dim.x = (trans[0].x * dim.x + trans[3].x) - trans[3].x;
    dim.y = (trans[1].y * dim.y + trans[3].y) - trans[3].y;
    const glm::ivec2 clipDim {static_cast<int>(std::round(dim.x)),
                              static_cast<int>(std::round(dim.y))};
    // A zero-rounded dim makes pushClipRect "extend to the screen edge" (disables
    // clipping → content bleeds past the panel); skip the draw, the same degenerate-
    // clip guard MadTileGrid/MadScrollView use.
    if (clipDim.x < 1 || clipDim.y < 1)
        return;
    mRenderer->pushClipRect(
        glm::ivec2 {static_cast<int>(std::round(trans[3].x)),
                    static_cast<int>(std::round(trans[3].y))},
        clipDim);

    glm::mat4 scrolledTrans {glm::translate(trans, glm::vec3 {0.0f, -mScrollOffset, 0.0f})};

    if (mSaveHeight > mScrollOffset)
        mSaveButton->render(scrolledTrans);

    for (size_t i {0}; i < mRows.size(); ++i) {
        const float rowTop {mSaveHeight +
                            static_cast<float>(static_cast<int>(i) / std::max(1, mColumns)) *
                                mRowHeight};
        if (rowTop + mRowHeight < mScrollOffset || rowTop > mScrollOffset + mSize.y)
            continue;
        mRows[i].title->render(scrolledTrans);
        mRows[i].description->render(scrolledTrans);
        mRows[i].identify->render(scrolledTrans);
        mRows[i].clear->render(scrolledTrans);
    }

    mRenderer->popClipRect();
}

std::vector<HelpPrompt> MadPlayerSlots::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    prompts.push_back(HelpPrompt("up/down/left/right", "choose"));
    prompts.push_back(HelpPrompt("a", "select"));
    return prompts;
}
