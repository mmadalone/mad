//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadSidebar.cpp
//
//  Section sidebar for the MAD control panel (deck-patches).
//

#include "guis/mad/MadSidebar.h"

#include <cmath>
#include "guis/mad/MadTheme.h"

MadSidebar::MadSidebar(const std::vector<std::string>& labels)
    : mRenderer {Renderer::getInstance()}
    , mActive {0}
    , mEntryHeight {0.0f}
    , mIconSize {0.0f}
    , mScrollOffset {0.0f}
{
    // Entries are rendered manually (clipped + scrolled), not as children.
    for (const std::string& label : labels) {
        Entry entry;
        entry.icon = std::make_shared<ImageComponent>();
        entry.icon->setOrigin(0.5f, 0.5f);
        entry.label = std::make_shared<TextComponent>(label, Font::get(FONT_SIZE_MINI),
                                                      MadTheme::color(MadColor::Secondary), ALIGN_CENTER,
                                                      ALIGN_CENTER, glm::ivec2 {0, 0});
        mEntries.emplace_back(entry);
    }
}

void MadSidebar::onSizeChanged()
{
    if (mEntries.empty())
        return;

    // Fixed icon box ≈ 0.14 × screen height (the Tk sidebar's ~112 px at 800p)
    // with the label underneath; entries don't shrink to fit — the column
    // scrolls instead, always keeping the active entry visible.
    const float labelHeight {Font::get(FONT_SIZE_MINI)->getHeight()};
    const float padding {Renderer::getScreenHeight() * 0.012f};
    mIconSize = std::min(Renderer::getScreenHeight() * 0.14f, mSize.x * 0.86f);
    mEntryHeight = mIconSize + labelHeight + padding * 2.0f;

    for (size_t i {0}; i < mEntries.size(); ++i) {
        const float cellTop {static_cast<float>(i) * mEntryHeight};
        mEntries[i].icon->setMaxSize(mIconSize, mIconSize);
        mEntries[i].icon->setPosition(mSize.x / 2.0f, cellTop + padding + mIconSize / 2.0f);
        mEntries[i].label->setPosition(0.0f, cellTop + padding + mIconSize);
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
        mEntries[i].label->setColor(active ? MadTheme::color(MadColor::Title) : MadTheme::color(MadColor::Secondary));
        mEntries[i].icon->setOpacity(active ? 1.0f : 0.6f);
        mEntries[i].label->setOpacity(active ? 1.0f : 0.75f);
    }
    keepActiveVisible();
}

void MadSidebar::keepActiveVisible()
{
    if (mEntries.empty() || mEntryHeight <= 0.0f || mSize.y <= 0.0f)
        return;

    const float cellTop {static_cast<float>(mActive) * mEntryHeight};
    const float cellBottom {cellTop + mEntryHeight};

    if (cellTop < mScrollOffset)
        mScrollOffset = cellTop;
    else if (cellBottom > mScrollOffset + mSize.y)
        mScrollOffset = cellBottom - mSize.y;

    const float maxOffset {
        std::max(0.0f, static_cast<float>(mEntries.size()) * mEntryHeight - mSize.y)};
    mScrollOffset = glm::clamp(mScrollOffset, 0.0f, maxOffset);
}

void MadSidebar::setIcon(const int index, const std::string& path)
{
    if (index < 0 || index >= static_cast<int>(mEntries.size()) || path.empty())
        return;
    mEntries[index].icon->setImage(path);
}

void MadSidebar::render(const glm::mat4& parentTrans)
{
    if (!isVisible() || mEntries.empty())
        return;

    glm::mat4 trans {parentTrans * getTransform()};

    // Clip to the sidebar column (same scheme as MadTileGrid: scale-aware).
    glm::vec3 dim {mSize.x, mSize.y, 0.0f};
    dim.x = (trans[0].x * dim.x + trans[3].x) - trans[3].x;
    dim.y = (trans[1].y * dim.y + trans[3].y) - trans[3].y;
    const glm::ivec2 clipDim {static_cast<int>(std::round(dim.x)),
                              static_cast<int>(std::round(dim.y))};
    // A zero-rounded dim makes pushClipRect "extend to the screen edge" (disables
    // clipping → rows bleed past the panel); skip the draw, the same degenerate-clip
    // guard MadTileGrid/MadScrollView use.
    if (clipDim.x < 1 || clipDim.y < 1)
        return;
    mRenderer->pushClipRect(
        glm::ivec2 {static_cast<int>(std::round(trans[3].x)),
                    static_cast<int>(std::round(trans[3].y))},
        clipDim);

    glm::mat4 scrolledTrans {glm::translate(trans, glm::vec3 {0.0f, -mScrollOffset, 0.0f})};

    if (mActive >= 0 && mActive < static_cast<int>(mEntries.size())) {
        const float cellTop {static_cast<float>(mActive) * mEntryHeight};
        mRenderer->setMatrix(scrolledTrans);
        mRenderer->drawRect(0.0f, cellTop, mSize.x, mEntryHeight, MadTheme::color(MadColor::ButtonFlatUnfocused),
                            MadTheme::color(MadColor::ButtonFlatUnfocused));
        const float accentWidth {std::max(2.0f, mSize.x * 0.035f)};
        mRenderer->drawRect(0.0f, cellTop, accentWidth, mEntryHeight, MadTheme::color(MadColor::Red),
                            MadTheme::color(MadColor::Red));
    }

    // Only render the entries that intersect the viewport.
    for (size_t i {0}; i < mEntries.size(); ++i) {
        const float cellTop {static_cast<float>(i) * mEntryHeight};
        if (cellTop + mEntryHeight < mScrollOffset || cellTop > mScrollOffset + mSize.y)
            continue;
        mEntries[i].icon->render(scrolledTrans);
        mEntries[i].label->render(scrolledTrans);
    }

    mRenderer->popClipRect();
}
