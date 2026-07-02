//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadToggleList.cpp
//
//  On/off toggle list for MAD control panel pages. See the header. Modeled on
//  MadReorderList's widget contract (self-contained focus target inside a
//  MadScrollView) with SwitchComponent glyphs for the rows.
//

#include "guis/mad/widgets/MadToggleList.h"

#include "Sound.h"
#include "guis/mad/MadTheme.h"

#include <algorithm>
#include <cmath>

MadToggleList::MadToggleList()
    : mRenderer {Renderer::getInstance()}
    , mCursor {0}
    , mFocused {false}
    , mRowHeight {0.0f}
{
}

void MadToggleList::setItems(const std::vector<Item>& items)
{
    mItems = items;
    mCursor = 0;
    rebuild();
}

void MadToggleList::setOnToggle(std::function<void(int, const std::string&, bool)> cb)
{
    mOnToggle = std::move(cb);
}

void MadToggleList::setRowValue(int index, bool value)
{
    // Guard on mSwitches (which is <= mItems and is the pointer we deref): a
    // zero-width rebuild() clears mSwitches while mItems stays populated.
    if (index < 0 || index >= static_cast<int>(mSwitches.size()))
        return;
    mItems[index].value = value;
    mSwitches[index]->setState(value);
}

bool MadToggleList::rowValue(int index) const
{
    if (index < 0 || index >= static_cast<int>(mItems.size()))
        return false;
    return mItems[index].value;
}

int MadToggleList::rowIndexOfKey(const std::string& key) const
{
    for (size_t i {0}; i < mItems.size(); ++i)
        if (mItems[i].key == key)
            return static_cast<int>(i);
    return -1;
}

void MadToggleList::setCursorIndex(int index)
{
    if (mItems.empty())
        mCursor = 0;
    else
        mCursor = glm::clamp(index, 0, static_cast<int>(mItems.size()) - 1);
}

float MadToggleList::contentHeight() const
{
    return static_cast<float>(mItems.size()) * mRowHeight;
}

glm::vec2 MadToggleList::cursorRowRect() const
{
    const float top {static_cast<float>(mCursor) * mRowHeight};
    return glm::vec2 {top, top + mRowHeight};
}

void MadToggleList::onSizeChanged()
{
    rebuild();
}

void MadToggleList::rebuild()
{
    mLabels.clear();
    mSwitches.clear();
    if (mSize.x <= 0.0f)
        return;

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};

    // Build the switches first so their natural height sets the row height.
    float switchHeight {smallHeight};
    for (const Item& item : mItems) {
        // Default-construct then setState (the upstream menu idiom): the ctor
        // stores the state but renders the OFF graphic — only setState syncs
        // the image.
        auto sw = std::make_shared<SwitchComponent>();
        sw->setState(item.value);
        switchHeight = std::max(switchHeight, sw->getSize().y);
        mSwitches.emplace_back(sw);
    }
    mRowHeight = std::max(smallHeight * 1.8f, switchHeight + smallHeight * 0.5f);

    const float switchWidth {mSwitches.empty() ? 0.0f : mSwitches.front()->getSize().x};
    const float rightMargin {smallHeight * 0.4f};
    const float switchX {mSize.x - switchWidth - rightMargin};

    for (size_t i {0}; i < mItems.size(); ++i) {
        const float rowTop {static_cast<float>(i) * mRowHeight};

        auto label = std::make_shared<TextComponent>(
            "  " + mItems[i].label, Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Primary),
            ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
        // The label column runs up to the switch, so a long label ellipsizes
        // instead of colliding with the glyph.
        label->setPosition(0.0f, rowTop);
        label->setSize(std::max(0.0f, switchX - smallHeight * 0.3f), mRowHeight);
        mLabels.emplace_back(label);

        mSwitches[i]->setPosition(switchX,
                                  rowTop + (mRowHeight - mSwitches[i]->getSize().y) * 0.5f);
    }
}

bool MadToggleList::input(InputConfig* config, Input input)
{
    // The size check keeps the A-branch's mSwitches[mCursor] deref safe even in
    // the degenerate zero-width state (rebuild() clears mSwitches, keeps mItems).
    if (mItems.empty() || mItems.size() != mSwitches.size() || input.value == 0)
        return false;

    if (config->isMappedLike("up", input) || config->isMappedLike("down", input)) {
        const int direction {config->isMappedLike("down", input) ? 1 : -1};
        const int target {mCursor + direction};
        if (target < 0 || target >= static_cast<int>(mItems.size()))
            return false; // Edge: the page moves focus to the adjacent control.
        mCursor = target;
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        return true;
    }
    if (config->isMappedTo("a", input)) {
        const bool next {!mItems[mCursor].value};
        mItems[mCursor].value = next;
        mSwitches[mCursor]->setState(next);
        NavigationSounds::getInstance().playThemeNavigationSound(SELECTSOUND);
        if (mOnToggle)
            mOnToggle(mCursor, mItems[mCursor].key, next);
        return true;
    }
    return false;
}

void MadToggleList::render(const glm::mat4& parentTrans)
{
    if (!isVisible() || mItems.empty())
        return;

    glm::mat4 trans {parentTrans * getTransform()};
    mRenderer->setMatrix(trans);

    const float height {mRowHeight};
    const float gap {std::max(1.0f, height * 0.06f)};
    for (size_t i {0}; i < mItems.size(); ++i)
        mRenderer->drawRect(0.0f, static_cast<float>(i) * height, mSize.x, height - gap,
                            MadTheme::color(MadColor::PanelDimmed),
                            MadTheme::color(MadColor::PanelDimmed));

    if (mFocused && mCursor >= 0 && mCursor < static_cast<int>(mItems.size())) {
        const float top {static_cast<float>(mCursor) * height};
        const float stroke {std::max(2.0f, 2.5f * Renderer::getScreenHeightModifier())};
        const unsigned int color {MadTheme::color(MadColor::HighlightAccent)};
        mRenderer->drawRect(0.0f, top, mSize.x, stroke, color, color);
        mRenderer->drawRect(0.0f, top + height - gap - stroke, mSize.x, stroke, color, color);
        mRenderer->drawRect(0.0f, top, stroke, height - gap, color, color);
        mRenderer->drawRect(mSize.x - stroke, top, stroke, height - gap, color, color);
    }

    for (const auto& label : mLabels)
        label->render(trans);
    for (const auto& sw : mSwitches)
        sw->render(trans);
}

std::vector<HelpPrompt> MadToggleList::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    prompts.push_back(HelpPrompt("up/down", "choose"));
    prompts.push_back(HelpPrompt("a", "toggle"));
    return prompts;
}
