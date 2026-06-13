//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadTileGrid.cpp
//
//  Scrollable grid of console-art tiles for the MAD control panel (deck-patches).
//

#include "guis/mad/widgets/MadTileGrid.h"

#include "Sound.h"

#include <cmath>
#include "guis/mad/MadTheme.h"

MadTileGrid::MadTileGrid()
    : mRenderer {Renderer::getInstance()}
    , mCursor {0}
    , mColumns {1}
    , mFocused {false}
    , mCellWidth {0.0f}
    , mCellHeight {0.0f}
    , mArtWidth {0.0f}
    , mArtHeight {0.0f}
    , mScrollOffset {0.0f}
{
}

void MadTileGrid::setTiles(const std::vector<Tile>& tiles)
{
    mEntries.clear();
    mCursor = 0;
    mScrollOffset = 0.0f;

    for (const Tile& tile : tiles) {
        TileEntry entry;
        entry.tile = tile;

        entry.image = std::make_shared<ImageComponent>();
        entry.image->setOrigin(0.5f, 0.5f);

        // {0,1} + setSize(w, 0): long labels ("Wii Remote + Nunchuk") WRAP
        // instead of ellipsizing; layoutTiles grows the cells when needed.
        entry.label = std::make_shared<TextComponent>(tile.label, Font::get(FONT_SIZE_SMALL),
                                                      MadTheme::color(MadColor::Primary), ALIGN_CENTER,
                                                      ALIGN_CENTER, glm::ivec2 {0, 1});

        // The badge bullet marks locally configured entries; warn marks a
        // problem state (red, e.g. a backend with an empty SDL whitelist).
        const std::string sublabelText {tile.badge ? "● " + tile.sublabel : tile.sublabel};
        entry.sublabel = std::make_shared<TextComponent>(
            sublabelText, Font::get(FONT_SIZE_MINI),
            tile.warn ? MadTheme::color(MadColor::Red) :
                        (tile.badge ? MadTheme::color(MadColor::Green) : MadTheme::color(MadColor::Secondary)),
            ALIGN_CENTER, ALIGN_CENTER, glm::ivec2 {0, 0});

        mEntries.emplace_back(entry);
    }

    layoutTiles();
}

void MadTileGrid::onSizeChanged()
{
    layoutTiles();
}

void MadTileGrid::layoutTiles()
{
    if (mEntries.empty() || mSize.x <= 0.0f || mSize.y <= 0.0f)
        return;

    const float heightModifier {Renderer::getScreenHeightModifier()};
    mArtWidth = 200.0f * heightModifier;
    mArtHeight = 120.0f * heightModifier;
    const float gap {24.0f * heightModifier};

    mColumns = std::max(1, static_cast<int>(std::floor(mSize.x / (mArtWidth + gap))));
    mCellWidth = mSize.x / static_cast<float>(mColumns);

    const float labelHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    const float sublabelHeight {Font::get(FONT_SIZE_MINI)->getHeight()};
    // Horizontal breathing room so a name ≈ cell width (e.g. "8BitDo FC30 II")
    // word-wraps WITHIN the cell instead of spilling off the side.
    const float labelInset {gap * 0.5f};

    // First pass: autosize (wrap) the labels so the tallest one decides the
    // uniform cell height — grids with only short labels stay pixel-identical.
    float maxLabelHeight {labelHeight};
    for (TileEntry& entry : mEntries) {
        entry.label->setSize(mCellWidth - labelInset * 2.0f, 0.0f);
        maxLabelHeight = std::max(maxLabelHeight, entry.label->getSize().y);
    }
    mCellHeight = mArtHeight + maxLabelHeight + sublabelHeight + gap;

    for (size_t i {0}; i < mEntries.size(); ++i) {
        const int col {static_cast<int>(i) % mColumns};
        const int row {static_cast<int>(i) / mColumns};
        const float cellX {static_cast<float>(col) * mCellWidth};
        const float cellY {static_cast<float>(row) * mCellHeight};

        TileEntry& entry {mEntries[i]};
        entry.image->setMaxSize(mArtWidth, mArtHeight);
        if (!entry.tile.artPath.empty())
            entry.image->setImage(entry.tile.artPath);
        entry.image->setPosition(cellX + mCellWidth / 2.0f, cellY + gap / 2.0f + mArtHeight / 2.0f);

        entry.label->setPosition(cellX + labelInset, cellY + gap / 2.0f + mArtHeight);
        // (size already set in the measuring pass — wrapped height kept)

        entry.sublabel->setPosition(cellX, cellY + gap / 2.0f + mArtHeight +
                                               entry.label->getSize().y);
        entry.sublabel->setSize(mCellWidth, sublabelHeight);
    }

    keepCursorVisible();
}

bool MadTileGrid::input(InputConfig* config, Input input)
{
    if (mEntries.empty() || input.value == 0)
        return false;

    if (config->isMappedLike("left", input)) {
        if (mCursor % mColumns > 0)
            moveCursor(-1);
        return true;
    }
    if (config->isMappedLike("right", input)) {
        if (mCursor % mColumns < mColumns - 1 && mCursor + 1 < static_cast<int>(mEntries.size()))
            moveCursor(1);
        return true;
    }
    if (config->isMappedLike("up", input)) {
        if (mCursor - mColumns >= 0)
            moveCursor(-mColumns);
        return true;
    }
    if (config->isMappedLike("down", input)) {
        if (mCursor + mColumns < static_cast<int>(mEntries.size()))
            moveCursor(mColumns);
        else if (mCursor / mColumns < rowCount() - 1)
            // A shorter last row below: clamp onto its last tile instead of
            // leaving those tiles unreachable from directly above.
            moveCursor(static_cast<int>(mEntries.size()) - 1 - mCursor);
        return true;
    }
    if (config->isMappedTo("a", input)) {
        NavigationSounds::getInstance().playThemeNavigationSound(SELECTSOUND);
        if (mOnPick)
            mOnPick(mEntries[mCursor].tile.key);
        return true;
    }

    return false;
}

void MadTileGrid::moveCursor(const int amount)
{
    const int target {
        glm::clamp(mCursor + amount, 0, static_cast<int>(mEntries.size()) - 1)};
    if (target == mCursor)
        return;
    mCursor = target;
    NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
    keepCursorVisible();
}

void MadTileGrid::setCursorIndex(const int index)
{
    if (mEntries.empty())
        return;
    mCursor = glm::clamp(index, 0, static_cast<int>(mEntries.size()) - 1);
    keepCursorVisible();
}

void MadTileGrid::pageScroll(const int direction)
{
    if (mEntries.empty() || mCellHeight <= 0.0f)
        return;
    const int rowsPerPage {std::max(1, static_cast<int>(mSize.y / mCellHeight))};
    moveCursor(direction * rowsPerPage * mColumns);
}

void MadTileGrid::keepCursorVisible()
{
    if (mEntries.empty() || mCellHeight <= 0.0f)
        return;

    const int row {mCursor / mColumns};
    const float rowTop {static_cast<float>(row) * mCellHeight};
    const float rowBottom {rowTop + mCellHeight};

    if (rowTop < mScrollOffset)
        mScrollOffset = rowTop;
    else if (rowBottom > mScrollOffset + mSize.y)
        mScrollOffset = rowBottom - mSize.y;

    const float maxOffset {
        std::max(0.0f, static_cast<float>(rowCount()) * mCellHeight - mSize.y)};
    mScrollOffset = glm::clamp(mScrollOffset, 0.0f, maxOffset);
}

void MadTileGrid::render(const glm::mat4& parentTrans)
{
    if (!isVisible() || mEntries.empty())
        return;

    glm::mat4 trans {parentTrans * getTransform()};

    // Clip to the viewport (same scheme as ComponentList: scale-aware dimensions).
    glm::vec3 dim {mSize.x, mSize.y, 0.0f};
    dim.x = (trans[0].x * dim.x + trans[3].x) - trans[3].x;
    dim.y = (trans[1].y * dim.y + trans[3].y) - trans[3].y;
    mRenderer->pushClipRect(
        glm::ivec2 {static_cast<int>(std::round(trans[3].x)),
                    static_cast<int>(std::round(trans[3].y))},
        glm::ivec2 {static_cast<int>(std::round(dim.x)), static_cast<int>(std::round(dim.y))});

    glm::mat4 scrolledTrans {glm::translate(trans, glm::vec3 {0.0f, -mScrollOffset, 0.0f})};

    // Focused tile: outline frame drawn as four strips in the selector color
    // (only while the grid itself holds the page focus).
    if (mFocused && mCursor >= 0 && mCursor < static_cast<int>(mEntries.size())) {
        const int col {mCursor % mColumns};
        const int row {mCursor / mColumns};
        const float inset {mCellWidth * 0.03f};
        const float frameX {static_cast<float>(col) * mCellWidth + inset};
        const float frameY {static_cast<float>(row) * mCellHeight + inset / 2.0f};
        const float frameWidth {mCellWidth - inset * 2.0f};
        const float frameHeight {mCellHeight - inset};
        const float stroke {std::max(2.0f, 3.0f * Renderer::getScreenHeightModifier())};

        mRenderer->setMatrix(scrolledTrans);
        mRenderer->drawRect(frameX, frameY, frameWidth, stroke, MadTheme::color(MadColor::Selector),
                            MadTheme::color(MadColor::Selector));
        mRenderer->drawRect(frameX, frameY + frameHeight - stroke, frameWidth, stroke,
                            MadTheme::color(MadColor::Selector), MadTheme::color(MadColor::Selector));
        mRenderer->drawRect(frameX, frameY, stroke, frameHeight, MadTheme::color(MadColor::Selector),
                            MadTheme::color(MadColor::Selector));
        mRenderer->drawRect(frameX + frameWidth - stroke, frameY, stroke, frameHeight,
                            MadTheme::color(MadColor::Selector), MadTheme::color(MadColor::Selector));
    }

    // Only render the rows that intersect the viewport.
    for (size_t i {0}; i < mEntries.size(); ++i) {
        const int row {static_cast<int>(i) / mColumns};
        const float rowTop {static_cast<float>(row) * mCellHeight};
        if (rowTop + mCellHeight < mScrollOffset || rowTop > mScrollOffset + mSize.y)
            continue;
        mEntries[i].image->render(scrolledTrans);
        mEntries[i].label->render(scrolledTrans);
        mEntries[i].sublabel->render(scrolledTrans);
    }

    mRenderer->popClipRect();
}

std::vector<HelpPrompt> MadTileGrid::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    prompts.push_back(HelpPrompt("up/down/left/right", "choose"));
    prompts.push_back(HelpPrompt("a", "select"));
    return prompts;
}
