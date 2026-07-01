//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadVirtualList.cpp
//
//  Virtualized single-column text list for the MAD control panel (deck-patches).
//

#include "guis/mad/widgets/MadVirtualList.h"

#include "Sound.h"

#include <algorithm>
#include <cmath>
#include "guis/mad/MadTheme.h"

namespace
{
    // Row band height (font line-height with a little breathing room). Tunable;
    // confirmed for readability on-device.
    constexpr float kRowHeightScale {1.3f};
    // Extra slots beyond the strictly-visible count so a partial bottom row and
    // a one-row over-scroll never reveal an unbuilt gap.
    constexpr int kSlotSlack {2};
} // namespace

MadVirtualList::MadVirtualList()
    : mRenderer {Renderer::getInstance()}
    , mFont {Font::get(FONT_SIZE_SMALL)}
    , mRowHeight {Font::get(FONT_SIZE_SMALL)->getHeight() * kRowHeightScale}
    , mTextInset {0.0f}
    , mTextVOffset {0.0f}
    , mCursor {0}
    , mTopRow {0}
    , mFocused {false}
    , mActiveSlots {0}
{
    // Centre the single line (height = mFont->getHeight()) within the taller row
    // band so text sits in the middle of the selector frame, not on its top edge.
    mTextVOffset = std::round(std::max(0.0f, mRowHeight - mFont->getHeight()) * 0.5f);
}

int MadVirtualList::screenCount() const
{
    if (mRowHeight <= 0.0f)
        return 1;
    return std::max(1, static_cast<int>(std::floor(mSize.y / mRowHeight)));
}

void MadVirtualList::keepCursorVisible()
{
    const int sc {screenCount()};
    if (mCursor < mTopRow)
        mTopRow = mCursor;
    else if (mCursor >= mTopRow + sc)
        mTopRow = mCursor - sc + 1;
    mTopRow = glm::clamp(mTopRow, 0, std::max(0, size() - sc));
}

void MadVirtualList::rebuildPool()
{
    mSlots.clear();
    mSlotRow.clear();
    mActiveSlots = 0;
    if (mSize.x <= 0.0f || mRowHeight <= 0.0f)
        return;
    const int poolSize {screenCount() + kSlotSlack};
    for (int j {0}; j < poolSize; ++j) {
        // autoCalcExtent {1,0} = single line, line breaks removed; setText's
        // maxLength (set in layoutWindow) abbreviates with an ellipsis so a long
        // MAME title never wraps to a second line and breaks the fixed row band.
        auto slot = std::make_shared<TextComponent>("", mFont, 0xFFFFFFFF, ALIGN_LEFT,
                                                    ALIGN_CENTER, glm::ivec2 {1, 0});
        slot->setPosition(mTextInset, static_cast<float>(j) * mRowHeight + mTextVOffset);
        mSlots.emplace_back(slot);
        mSlotRow.emplace_back(-1);
    }
}

void MadVirtualList::layoutWindow()
{
    if (mSlots.empty())
        rebuildPool();
    mActiveSlots = 0;
    const int n {size()};
    const int poolSize {static_cast<int>(mSlots.size())};
    if (n == 0 || poolSize == 0)
        return;

    mTopRow = glm::clamp(mTopRow, 0, std::max(0, n - screenCount()));
    const float maxLength {std::max(0.0f, mSize.x - mTextInset * 2.0f)};
    for (int j {0}; j < poolSize; ++j) {
        const int rowIndex {mTopRow + j};
        if (rowIndex >= n)
            break;
        const std::shared_ptr<TextComponent>& slot {mSlots[j]};
        if (mSlotRow[j] != rowIndex) {
            slot->setText(mRows[rowIndex].text, true, maxLength);
            slot->setColor(mRows[rowIndex].color);
            mSlotRow[j] = rowIndex;
        }
        slot->setPosition(mTextInset, static_cast<float>(j) * mRowHeight + mTextVOffset);
        mActiveSlots = j + 1;
    }
}

void MadVirtualList::setRows(const std::vector<Row>& rows, const bool keepCursor)
{
    mRows = rows;
    if (keepCursor)
        mCursor = glm::clamp(mCursor, 0, std::max(0, size() - 1));
    else
        mCursor = 0;
    // The row->slot mapping is now stale; force every slot to re-shape its text.
    std::fill(mSlotRow.begin(), mSlotRow.end(), -1);
    keepCursorVisible();
    layoutWindow();
    if (mOnCursorChanged)
        mOnCursorChanged(mCursor);
}

void MadVirtualList::setRow(const int index, const std::string& text, const unsigned int color)
{
    if (index < 0 || index >= size())
        return;
    mRows[index].text = text;
    mRows[index].color = color;
    // Refresh the on-screen slot in place if this row is visible (no rebuild,
    // no cursor move) — the toggle relabel path.
    const int j {index - mTopRow};
    if (j >= 0 && j < mActiveSlots && j < static_cast<int>(mSlots.size())) {
        const float maxLength {std::max(0.0f, mSize.x - mTextInset * 2.0f)};
        mSlots[j]->setText(text, true, maxLength);
        mSlots[j]->setColor(color);
        // mSlotRow[j] still == index — unchanged.
    }
}

void MadVirtualList::clear()
{
    mRows.clear();
    mCursor = 0;
    mTopRow = 0;
    mActiveSlots = 0;
    std::fill(mSlotRow.begin(), mSlotRow.end(), -1);
}

void MadVirtualList::setCursor(const int index)
{
    if (size() == 0) {
        mCursor = 0;
        return;
    }
    mCursor = glm::clamp(index, 0, size() - 1);
    keepCursorVisible();
    layoutWindow();
    if (mOnCursorChanged)
        mOnCursorChanged(mCursor);
}

void MadVirtualList::moveCursor(const int delta)
{
    if (size() == 0)
        return;
    const int target {glm::clamp(mCursor + delta, 0, size() - 1)};
    if (target == mCursor)
        return;
    mCursor = target;
    keepCursorVisible();
    layoutWindow();
    NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
    if (mOnCursorChanged)
        mOnCursorChanged(mCursor);
}

void MadVirtualList::pageScroll(const int direction)
{
    if (size() == 0)
        return;
    moveCursor(direction * std::max(1, screenCount()));
}

void MadVirtualList::onSizeChanged()
{
    mTextInset = std::max(6.0f, mSize.x * 0.012f);
    rebuildPool();
    keepCursorVisible();
    layoutWindow();
}

bool MadVirtualList::input(InputConfig* config, Input input)
{
    if (mRows.empty() || input.value == 0)
        return false;

    if (config->isMappedLike("up", input)) {
        moveCursor(-1);
        return true;
    }
    if (config->isMappedLike("down", input)) {
        moveCursor(1);
        return true;
    }
    if (config->isMappedTo("a", input)) {
        NavigationSounds::getInstance().playThemeNavigationSound(SELECTSOUND);
        if (mOnSelect)
            mOnSelect(mCursor);
        return true;
    }
    return false;
}

void MadVirtualList::render(const glm::mat4& parentTrans)
{
    if (!isVisible() || mRows.empty())
        return;

    glm::mat4 trans {parentTrans * getTransform()};

    // Clip to the widget (same scale-aware scheme as MadTileGrid/MadScrollView).
    glm::vec3 dim {mSize.x, mSize.y, 0.0f};
    dim.x = (trans[0].x * dim.x + trans[3].x) - trans[3].x;
    dim.y = (trans[1].y * dim.y + trans[3].y) - trans[3].y;
    const glm::ivec2 clipDim {static_cast<int>(std::round(dim.x)),
                              static_cast<int>(std::round(dim.y))};
    // A zero clip dimension is treated as "extend to the screen edge" (clipping
    // OFF) — skip the draw instead, the same degenerate-clip guard the siblings use.
    if (clipDim.x < 1 || clipDim.y < 1)
        return;
    mRenderer->pushClipRect(glm::ivec2 {static_cast<int>(std::round(trans[3].x)),
                                        static_cast<int>(std::round(trans[3].y))},
                            clipDim);

    // Focused row: outline frame in the selector color (four strips), drawn at
    // its window-local band. keepCursorVisible guarantees the cursor is in view.
    if (mFocused && mCursor >= mTopRow && mCursor < mTopRow + mActiveSlots) {
        const float top {static_cast<float>(mCursor - mTopRow) * mRowHeight};
        const float stroke {std::max(2.0f, 2.5f * Renderer::getScreenHeightModifier())};
        const unsigned int c {MadTheme::color(MadColor::HighlightAccent)};
        mRenderer->setMatrix(trans);
        mRenderer->drawRect(0.0f, top, mSize.x, stroke, c, c);
        mRenderer->drawRect(0.0f, top + mRowHeight - stroke, mSize.x, stroke, c, c);
        mRenderer->drawRect(0.0f, top, stroke, mRowHeight, c, c);
        mRenderer->drawRect(mSize.x - stroke, top, stroke, mRowHeight, c, c);
    }

    for (int j {0}; j < mActiveSlots; ++j)
        mSlots[j]->render(trans);

    // Slim scrollbar at the right edge whenever there is more than one screenful.
    if (overflows()) {
        const float barWidth {std::max(2.0f, 4.0f * Renderer::getScreenHeightModifier())};
        const float barX {mSize.x - barWidth};
        const int sc {screenCount()};
        const float thumbHeight {std::max(mSize.y * 0.05f,
                                          mSize.y * (static_cast<float>(sc) /
                                                     static_cast<float>(size())))};
        const int denom {std::max(1, size() - sc)};
        const float thumbY {(static_cast<float>(mTopRow) / static_cast<float>(denom)) *
                            (mSize.y - thumbHeight)};
        mRenderer->setMatrix(trans);
        mRenderer->drawRect(barX, 0.0f, barWidth, mSize.y, MadTheme::color(MadColor::Separators),
                            MadTheme::color(MadColor::Separators));
        mRenderer->drawRect(barX, thumbY, barWidth, thumbHeight, MadTheme::color(MadColor::Selector),
                            MadTheme::color(MadColor::Selector));
    }

    mRenderer->popClipRect();
}

std::vector<HelpPrompt> MadVirtualList::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    prompts.push_back(HelpPrompt("up/down", "choose"));
    prompts.push_back(HelpPrompt("a", "select"));
    if (overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}
