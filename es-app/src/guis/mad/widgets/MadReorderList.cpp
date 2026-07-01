//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadReorderList.cpp
//
//  Carry-mode reorder list for the MAD control panel Priority editor.
//

#include "guis/mad/widgets/MadReorderList.h"

#include "Sound.h"

#include <cmath>
#include "guis/mad/MadTheme.h"

MadReorderList::MadReorderList()
    : mRenderer {Renderer::getInstance()}
    , mCursor {0}
    , mPreLiftCursor {0}
    , mCarrying {false}
    , mFocused {false}
    , mPlayerTags {true}
{
}

float MadReorderList::rowHeight() const
{
    return Font::get(FONT_SIZE_SMALL)->getHeight() * 1.8f;
}

float MadReorderList::contentHeight() const
{
    return static_cast<float>(mItems.size()) * rowHeight();
}

glm::vec2 MadReorderList::cursorRowRect() const
{
    const float top {static_cast<float>(mCursor) * rowHeight()};
    return glm::vec2 {top, top + rowHeight()};
}

void MadReorderList::setItems(const std::vector<std::string>& items)
{
    mItems = items;
    mHidden.assign(mItems.size(), false); // realign; caller sets real flags via setHidden
    mCursor = 0;
    mCarrying = false;
    rebuildTexts();
}

std::vector<std::string> MadReorderList::items() const
{
    return mItems;
}

void MadReorderList::setPlayerTags(bool on)
{
    mPlayerTags = on;
    rebuildTexts();
}

void MadReorderList::setHidden(const std::vector<bool>& hidden)
{
    mHidden = hidden;
    mHidden.resize(mItems.size(), false); // keep parallel to mItems
    rebuildTexts();
}

void MadReorderList::setRowHidden(int index, bool hidden)
{
    if (index < 0 || index >= static_cast<int>(mHidden.size()))
        return;
    mHidden[index] = hidden;
    rebuildTexts();
}

bool MadReorderList::rowHidden(int index) const
{
    return index >= 0 && index < static_cast<int>(mHidden.size()) && mHidden[index];
}

void MadReorderList::setOnToggle(std::function<void(int)> cb)
{
    mOnToggle = std::move(cb);
}

void MadReorderList::cancelCarry()
{
    if (!mCarrying)
        return;
    mItems = mPreLift;
    mHidden = mPreLiftHidden;
    // Back to the row the lift started on, not wherever it was carried to.
    mCursor = glm::clamp(mPreLiftCursor, 0, static_cast<int>(mItems.size()) - 1);
    mCarrying = false;
    rebuildTexts();
    NavigationSounds::getInstance().playThemeNavigationSound(BACKSOUND);
}

void MadReorderList::onSizeChanged()
{
    rebuildTexts();
}

void MadReorderList::rebuildTexts()
{
    mTexts.clear();
    if (mSize.x <= 0.0f)
        return;
    const float height {rowHeight()};
    for (size_t i {0}; i < mItems.size(); ++i) {
        std::string label;
        unsigned int color;
        if (mPlayerTags) {
            const std::string tag {i == 0 ? "P1" : (i == 1 ? "P2" : "#" + std::to_string(i + 1))};
            label = "  " + tag + "   " + mItems[i];
            color = i == 0 ? MadTheme::color(MadColor::Green) : MadTheme::color(MadColor::Primary);
        }
        else {
            const bool hidden {i < mHidden.size() && mHidden[i]};
            label = "  " + mItems[i] + (hidden ? "   (hidden)" : "");
            // Secondary is dimmer than Primary in the bundled theme (HelpText can equal
            // Primary there, leaving no visible dim) — the "(hidden)" suffix is the guarantee.
            color = hidden ? MadTheme::color(MadColor::Secondary) : MadTheme::color(MadColor::Primary);
        }
        auto text = std::make_shared<TextComponent>(
            label, Font::get(FONT_SIZE_SMALL), color, ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
        text->setPosition(0.0f, static_cast<float>(i) * height);
        text->setSize(mSize.x, height);
        mTexts.emplace_back(text);
    }
}

bool MadReorderList::input(InputConfig* config, Input input)
{
    if (mItems.empty() || input.value == 0)
        return false;

    if (config->isMappedLike("up", input) || config->isMappedLike("down", input)) {
        const int direction {config->isMappedLike("down", input) ? 1 : -1};
        const int target {mCursor + direction};
        if (target < 0 || target >= static_cast<int>(mItems.size()))
            return false; // Edge: the page moves focus to the adjacent control.
        if (mCarrying) {
            std::swap(mItems[mCursor], mItems[target]);
            if (mCursor < static_cast<int>(mHidden.size()) &&
                target < static_cast<int>(mHidden.size())) {
                bool a {mHidden[mCursor]}, b {mHidden[target]};
                mHidden[mCursor] = b;
                mHidden[target] = a; // carry the hidden flag with its row
            }
        }
        mCursor = target;
        if (mCarrying)
            rebuildTexts();
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        return true;
    }
    if (config->isMappedTo("a", input)) {
        if (!mCarrying) {
            mPreLift = mItems; // Lift: remember order + flags + cursor for B-cancel.
            mPreLiftHidden = mHidden;
            mPreLiftCursor = mCursor;
        }
        mCarrying = !mCarrying;
        NavigationSounds::getInstance().playThemeNavigationSound(SELECTSOUND);
        return true;
    }
    if (!mCarrying && mOnToggle && config->isMappedTo("x", input)) {
        mOnToggle(mCursor); // page flips this row's mode + calls setRowHidden
        return true;
    }
    return false;
}

void MadReorderList::render(const glm::mat4& parentTrans)
{
    if (!isVisible() || mItems.empty())
        return;

    glm::mat4 trans {parentTrans * getTransform()};
    mRenderer->setMatrix(trans);

    const float height {rowHeight()};
    const float gap {std::max(1.0f, height * 0.06f)};
    for (size_t i {0}; i < mItems.size(); ++i)
        mRenderer->drawRect(0.0f, static_cast<float>(i) * height, mSize.x, height - gap,
                            MadTheme::color(MadColor::PanelDimmed), MadTheme::color(MadColor::PanelDimmed));

    if (mFocused && mCursor >= 0 && mCursor < static_cast<int>(mItems.size())) {
        const float top {static_cast<float>(mCursor) * height};
        if (mCarrying) {
            // The carried row: filled selector strip on the left + outline.
            mRenderer->drawRect(0.0f, top, mSize.x * 0.008f, height - gap,
                                MadTheme::color(MadColor::Green), MadTheme::color(MadColor::Green));
        }
        const float stroke {std::max(2.0f, 2.5f * Renderer::getScreenHeightModifier())};
        const unsigned int color {mCarrying ? MadTheme::color(MadColor::Green) : MadTheme::color(MadColor::HighlightAccent)};
        mRenderer->drawRect(0.0f, top, mSize.x, stroke, color, color);
        mRenderer->drawRect(0.0f, top + height - gap - stroke, mSize.x, stroke, color, color);
        mRenderer->drawRect(0.0f, top, stroke, height - gap, color, color);
        mRenderer->drawRect(mSize.x - stroke, top, stroke, height - gap, color, color);
    }

    for (const auto& text : mTexts)
        text->render(trans);
}

std::vector<HelpPrompt> MadReorderList::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (mCarrying) {
        prompts.push_back(HelpPrompt("up/down", "move row"));
        prompts.push_back(HelpPrompt("a", "drop"));
        prompts.push_back(HelpPrompt("b", "cancel"));
    }
    else {
        prompts.push_back(HelpPrompt("up/down", "choose"));
        prompts.push_back(HelpPrompt("a", "lift row"));
        if (mOnToggle)
            prompts.push_back(HelpPrompt("x", "show/hide"));
    }
    return prompts;
}
