//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadSidebar.cpp
//
//  Section sidebar for the MAD control panel (deck-patches).
//

#include "guis/mad/MadSidebar.h"

MadSidebar::MadSidebar(const std::vector<std::string>& labels)
    : mRenderer {Renderer::getInstance()}
    , mActive {0}
    , mEntryHeight {0.0f}
    , mIconSize {0.0f}
{
    for (const std::string& label : labels) {
        Entry entry;
        entry.icon = std::make_shared<ImageComponent>();
        entry.icon->setOrigin(0.5f, 0.5f);
        entry.label = std::make_shared<TextComponent>(label, Font::get(FONT_SIZE_MINI),
                                                      mMenuColorSecondary, ALIGN_CENTER,
                                                      ALIGN_CENTER, glm::ivec2 {0, 0});
        addChild(entry.icon.get());
        addChild(entry.label.get());
        mEntries.emplace_back(entry);
    }
}

void MadSidebar::onSizeChanged()
{
    if (mEntries.empty())
        return;

    mEntryHeight = mSize.y / static_cast<float>(mEntries.size());
    const float labelHeight {Font::get(FONT_SIZE_MINI)->getHeight()};
    // Icons sized so all entries fit the sidebar height with the label below.
    mIconSize = std::min(mEntryHeight - labelHeight - mEntryHeight * 0.16f, mSize.x * 0.45f);

    for (size_t i {0}; i < mEntries.size(); ++i) {
        const float cellTop {static_cast<float>(i) * mEntryHeight};
        const float contentHeight {mIconSize + labelHeight};
        const float iconCenterY {cellTop + (mEntryHeight - contentHeight) / 2.0f +
                                 mIconSize / 2.0f};
        mEntries[i].icon->setMaxSize(mIconSize, mIconSize);
        mEntries[i].icon->setPosition(mSize.x / 2.0f, iconCenterY);
        mEntries[i].label->setPosition(0.0f, iconCenterY + mIconSize / 2.0f);
        mEntries[i].label->setSize(mSize.x, labelHeight);
    }

    setActive(mActive);
}

void MadSidebar::setActive(const int index)
{
    if (index < 0 || index >= static_cast<int>(mEntries.size()))
        return;
    mActive = index;
    for (size_t i {0}; i < mEntries.size(); ++i) {
        const bool active {static_cast<int>(i) == mActive};
        mEntries[i].label->setColor(active ? mMenuColorTitle : mMenuColorSecondary);
        mEntries[i].icon->setOpacity(active ? 1.0f : 0.6f);
        mEntries[i].label->setOpacity(active ? 1.0f : 0.75f);
    }
}

void MadSidebar::setIcon(const int index, const std::string& path)
{
    if (index < 0 || index >= static_cast<int>(mEntries.size()) || path.empty())
        return;
    mEntries[index].icon->setImage(path);
}

void MadSidebar::render(const glm::mat4& parentTrans)
{
    if (!isVisible())
        return;

    glm::mat4 trans {parentTrans * getTransform()};
    mRenderer->setMatrix(trans);

    if (mActive >= 0 && mActive < static_cast<int>(mEntries.size())) {
        const float cellTop {static_cast<float>(mActive) * mEntryHeight};
        mRenderer->drawRect(0.0f, cellTop, mSize.x, mEntryHeight, mMenuColorButtonFlatUnfocused,
                            mMenuColorButtonFlatUnfocused);
        const float accentWidth {std::max(2.0f, mSize.x * 0.035f)};
        mRenderer->drawRect(0.0f, cellTop, accentWidth, mEntryHeight, mMenuColorRed,
                            mMenuColorRed);
    }

    renderChildren(trans);
}
