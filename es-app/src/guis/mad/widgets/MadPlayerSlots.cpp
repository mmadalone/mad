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

MadPlayerSlots::MadPlayerSlots()
    : mRenderer {Renderer::getInstance()}
    , mFocused {false}
    , mFocusRow {0}
    , mFocusCol {0}
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
            "Player " + std::to_string(player), Font::get(FONT_SIZE_SMALL), mMenuColorTitle,
            ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
        row.description = std::make_shared<TextComponent>(
            "  (unpinned)", Font::get(FONT_SIZE_SMALL), mMenuColorSecondary, ALIGN_LEFT,
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

    mSaveHeight = buttonHeight + pad * 2.0f;
    mRowHeight = lineHeight * 2.0f + buttonHeight + pad * 2.0f;

    mSaveButton->setPosition(0.0f, pad);

    for (size_t i {0}; i < mRows.size(); ++i) {
        const float rowTop {mSaveHeight + static_cast<float>(i) * mRowHeight};
        Row& row {mRows[i]};
        row.title->setPosition(0.0f, rowTop + pad);
        row.title->setSize(mSize.x, lineHeight);
        row.description->setPosition(0.0f, rowTop + pad + lineHeight);
        row.description->setSize(mSize.x, lineHeight);
        row.identify->setPosition(0.0f, rowTop + pad + lineHeight * 2.0f);
        row.clear->setPosition(row.identify->getSize().x + pad,
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
            mRows[i].description->setColor(mMenuColorSecondary);
            continue;
        }
        std::string badge;
        const std::string name {describePin(it->second, badge)};
        mRows[i].description->setText("  " + name + " · " + badge);
        // ✓ MAC pins are port-agnostic (accent); everything else is a warning.
        mRows[i].description->setColor(it->second.rfind("uniq:", 0) == 0 ? mMenuColorGreen :
                                                                           mMenuColorRed);
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

    const float top {mFocusRow == 0 ? 0.0f :
                                      mSaveHeight + static_cast<float>(mFocusRow - 1) * mRowHeight};
    const float bottom {mFocusRow == 0 ? mSaveHeight : top + mRowHeight};

    if (top < mScrollOffset)
        mScrollOffset = top;
    else if (bottom > mScrollOffset + mSize.y)
        mScrollOffset = bottom - mSize.y;

    const float contentHeight {mSaveHeight + static_cast<float>(PLAYER_COUNT) * mRowHeight};
    mScrollOffset = glm::clamp(mScrollOffset, 0.0f, std::max(0.0f, contentHeight - mSize.y));
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

    if (config->isMappedLike("up", input)) {
        if (mFocusRow == 0)
            return false; // Edge: the page moves focus to whatever sits above.
        --mFocusRow;
        mFocusCol = 0;
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        applyFocus();
        keepRowVisible();
        return true;
    }
    if (config->isMappedLike("down", input)) {
        if (mFocusRow >= PLAYER_COUNT)
            return false; // Edge: the page moves focus to whatever sits below.
        ++mFocusRow;
        mFocusCol = 0;
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        applyFocus();
        keepRowVisible();
        return true;
    }
    if (config->isMappedLike("left", input)) {
        if (mFocusRow > 0 && mFocusCol == 1) {
            mFocusCol = 0;
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            applyFocus();
        }
        return true;
    }
    if (config->isMappedLike("right", input)) {
        if (mFocusRow > 0 && mFocusCol == 0) {
            mFocusCol = 1;
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            applyFocus();
        }
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
    mRenderer->pushClipRect(
        glm::ivec2 {static_cast<int>(std::round(trans[3].x)),
                    static_cast<int>(std::round(trans[3].y))},
        glm::ivec2 {static_cast<int>(std::round(dim.x)), static_cast<int>(std::round(dim.y))});

    glm::mat4 scrolledTrans {glm::translate(trans, glm::vec3 {0.0f, -mScrollOffset, 0.0f})};

    if (mSaveHeight > mScrollOffset)
        mSaveButton->render(scrolledTrans);

    for (size_t i {0}; i < mRows.size(); ++i) {
        const float rowTop {mSaveHeight + static_cast<float>(i) * mRowHeight};
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
