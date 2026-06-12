//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadScrollView.cpp
//
//  Whole-content scroll container for MAD control panel pages (deck-patches).
//

#include "guis/mad/widgets/MadScrollView.h"

#include <cmath>
#include "guis/mad/MadTheme.h"

MadScrollView::MadScrollView()
    : mRenderer {Renderer::getInstance()}
    , mContentHeight {0.0f}
    , mScrollOffset {0.0f}
{
}

float MadScrollView::clampOffset(const float offset) const
{
    return glm::clamp(offset, 0.0f, std::max(0.0f, mContentHeight - mSize.y));
}

void MadScrollView::setContentHeight(const float height)
{
    mContentHeight = std::max(0.0f, height);
    mScrollOffset = clampOffset(mScrollOffset);
}

void MadScrollView::setScrollOffset(const float offset)
{
    mScrollOffset = clampOffset(offset);
}

void MadScrollView::onSizeChanged()
{
    mScrollOffset = clampOffset(mScrollOffset);
}

bool MadScrollView::ensureVisible(const float top, const float bottom)
{
    float offset {mScrollOffset};
    if (top < offset)
        offset = top;
    else if (bottom > offset + mSize.y)
        offset = bottom - mSize.y;
    offset = clampOffset(offset);
    if (offset == mScrollOffset)
        return false;
    mScrollOffset = offset;
    return true;
}

bool MadScrollView::pageScroll(const int direction)
{
    const float offset {
        clampOffset(mScrollOffset + static_cast<float>(direction) * mSize.y * 0.85f)};
    if (offset == mScrollOffset)
        return false;
    mScrollOffset = offset;
    return true;
}

void MadScrollView::render(const glm::mat4& parentTrans)
{
    if (!isVisible())
        return;

    glm::mat4 trans {parentTrans * getTransform()};

    // Clip to the view bounds (same scheme as MadTileGrid: scale-aware dims).
    glm::vec3 dim {mSize.x, mSize.y, 0.0f};
    dim.x = (trans[0].x * dim.x + trans[3].x) - trans[3].x;
    dim.y = (trans[1].y * dim.y + trans[3].y) - trans[3].y;
    const glm::ivec2 clipDim {static_cast<int>(std::round(dim.x)),
                              static_cast<int>(std::round(dim.y))};
    // pushClipRect treats a zero dimension as "extend to the screen edge",
    // which would DISABLE clipping — a degenerate view draws nothing instead.
    if (clipDim.x < 1 || clipDim.y < 1)
        return;
    mRenderer->pushClipRect(glm::ivec2 {static_cast<int>(std::round(trans[3].x)),
                                        static_cast<int>(std::round(trans[3].y))},
                            clipDim);

    glm::mat4 scrolledTrans {glm::translate(trans, glm::vec3 {0.0f, -mScrollOffset, 0.0f})};
    // No off-window culling: full-height children (grid/slots) draw their
    // offscreen rows too and the scissor discards the pixels. Accepted —
    // tens of quads at most; revisit with a visible-window hint if a future
    // page (Backends) ever holds hundreds of rows.
    renderChildren(scrolledTrans);

    // Slim scrollbar at the right edge whenever there is more content than view.
    if (overflows()) {
        const float barWidth {std::max(2.0f, 4.0f * Renderer::getScreenHeightModifier())};
        const float barX {mSize.x - barWidth};
        const float thumbHeight {
            std::max(mSize.y * 0.05f, mSize.y * (mSize.y / mContentHeight))};
        const float thumbY {(mScrollOffset / (mContentHeight - mSize.y)) *
                            (mSize.y - thumbHeight)};
        mRenderer->setMatrix(trans);
        mRenderer->drawRect(barX, 0.0f, barWidth, mSize.y, MadTheme::color(MadColor::Separators),
                            MadTheme::color(MadColor::Separators));
        mRenderer->drawRect(barX, thumbY, barWidth, thumbHeight, MadTheme::color(MadColor::Selector),
                            MadTheme::color(MadColor::Selector));
    }

    mRenderer->popClipRect();
}
