//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadChipRow.cpp
//
//  Horizontal row of toggle chips for the MAD control panel (deck-patches).
//

#include "guis/mad/widgets/MadChipRow.h"

#include "Sound.h"

#include <cmath>
#include "guis/mad/MadTheme.h"

MadChipRow::MadChipRow()
    : mRenderer {Renderer::getInstance()}
    , mCursor {0}
    , mFocused {false}
    , mMomentary {false}
    , mContentHeight {0.0f}
{
}

void MadChipRow::setChips(const std::vector<Chip>& chips)
{
    mEntries.clear();
    mCursor = 0;
    for (const Chip& chip : chips) {
        Entry entry;
        entry.chip = chip;
        entry.text = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                                     MadTheme::color(MadColor::Secondary), ALIGN_CENTER,
                                                     ALIGN_CENTER, glm::ivec2 {0, 0});
        refreshChip(entry);
        mEntries.emplace_back(entry);
    }
    layout();
}

void MadChipRow::refreshChip(Entry& entry)
{
    if (mMomentary) {
        entry.text->setText(entry.chip.label);
        entry.text->setColor(MadTheme::color(MadColor::Primary));
        return;
    }
    entry.text->setText((entry.chip.on ? "✓ " : "· ") + entry.chip.label);
    entry.text->setColor(entry.chip.on ? MadTheme::color(MadColor::Green) : MadTheme::color(MadColor::Secondary));
}

void MadChipRow::setChipState(const std::string& value, const bool on)
{
    for (Entry& entry : mEntries) {
        if (entry.chip.value == value && entry.chip.on != on) {
            entry.chip.on = on;
            refreshChip(entry);
        }
    }
}

void MadChipRow::setChipLabel(const std::string& value, const std::string& label)
{
    for (Entry& entry : mEntries) {
        if (entry.chip.value == value && entry.chip.label != label) {
            entry.chip.label = label;
            refreshChip(entry);
            layout();
        }
    }
}

void MadChipRow::onSizeChanged()
{
    layout();
}

void MadChipRow::layout()
{
    if (mEntries.empty() || mSize.x <= 0.0f) {
        mContentHeight = 0.0f;
        return;
    }

    const float padX {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.8f};
    const float lineHeight {Font::get(FONT_SIZE_SMALL)->getHeight() * 1.7f};
    const float gap {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.3f};

    float x {0.0f};
    float y {0.0f};
    for (Entry& entry : mEntries) {
        // Width from the widest of the two states so toggling doesn't reflow.
        const float textWidth {
            std::max(Font::get(FONT_SIZE_SMALL)->sizeText("✓ " + entry.chip.label).x,
                     Font::get(FONT_SIZE_SMALL)->sizeText("· " + entry.chip.label).x)};
        const float chipWidth {textWidth + padX * 2.0f};
        if (x > 0.0f && x + chipWidth > mSize.x) { // Wrap onto the next line.
            x = 0.0f;
            y += lineHeight + gap;
        }
        entry.pos = {x, y};
        entry.size = {chipWidth, lineHeight};
        entry.text->setPosition(x, y);
        entry.text->setSize(chipWidth, lineHeight);
        x += chipWidth + gap;
    }
    mContentHeight = y + lineHeight;
}

bool MadChipRow::input(InputConfig* config, Input input)
{
    if (mEntries.empty() || input.value == 0)
        return false;

    const glm::vec2 cur {mEntries[mCursor].pos};
    const float curCenterX {cur.x + mEntries[mCursor].size.x * 0.5f};

    // left/right walk a single line and stay inside the row at its ends; up/down
    // move between wrapped lines and only leave the row past its top/bottom edge —
    // true 4-way nav over the chip grid.
    if (config->isMappedLike("left", input)) {
        if (mCursor > 0 && mEntries[mCursor - 1].pos.y == cur.y) {
            --mCursor;
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        }
        return true;
    }
    if (config->isMappedLike("right", input)) {
        if (mCursor < static_cast<int>(mEntries.size()) - 1 &&
            mEntries[mCursor + 1].pos.y == cur.y) {
            ++mCursor;
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        }
        return true;
    }
    if (config->isMappedLike("up", input)) {
        const int t {nearestOnAdjacentLine(cur.y, curCenterX, -1)};
        if (t < 0)
            return false; // top line — let the page move to the control above
        mCursor = t;
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        return true;
    }
    if (config->isMappedLike("down", input)) {
        const int t {nearestOnAdjacentLine(cur.y, curCenterX, 1)};
        if (t < 0)
            return false; // bottom line — let the page move to the control below
        mCursor = t;
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        return true;
    }
    if (config->isMappedTo("a", input)) {
        Entry& entry {mEntries[mCursor]};
        if (!mMomentary) {
            entry.chip.on = !entry.chip.on; // Optimistic; the page reverts on failure.
            refreshChip(entry);
        }
        NavigationSounds::getInstance().playThemeNavigationSound(SELECTSOUND);
        if (mOnToggle)
            mOnToggle(entry.chip.value, mMomentary ? true : entry.chip.on);
        return true;
    }
    return false;
}

int MadChipRow::nearestOnAdjacentLine(const float fromY, const float centerX,
                                      const int dir) const
{
    bool found {false};
    float lineY {0.0f};
    for (const Entry& e : mEntries) {
        const bool candidate {dir < 0 ? e.pos.y < fromY : e.pos.y > fromY};
        if (!candidate)
            continue;
        if (!found || (dir < 0 ? e.pos.y > lineY : e.pos.y < lineY)) {
            lineY = e.pos.y;
            found = true;
        }
    }
    if (!found)
        return -1;
    int best {-1};
    float bestDx {0.0f};
    for (size_t i {0}; i < mEntries.size(); ++i) {
        if (mEntries[i].pos.y != lineY)
            continue;
        const float cx {mEntries[i].pos.x + mEntries[i].size.x * 0.5f};
        const float dx {std::fabs(cx - centerX)};
        if (best < 0 || dx < bestDx) {
            best = static_cast<int>(i);
            bestDx = dx;
        }
    }
    return best;
}

void MadChipRow::render(const glm::mat4& parentTrans)
{
    if (!isVisible() || mEntries.empty())
        return;

    glm::mat4 trans {parentTrans * getTransform()};
    mRenderer->setMatrix(trans);

    for (const Entry& entry : mEntries)
        mRenderer->drawRect(entry.pos.x, entry.pos.y, entry.size.x, entry.size.y,
                            MadTheme::color(MadColor::PanelDimmed), MadTheme::color(MadColor::PanelDimmed));

    if (mFocused && mCursor >= 0 && mCursor < static_cast<int>(mEntries.size())) {
        const Entry& entry {mEntries[mCursor]};
        const float stroke {std::max(2.0f, 2.5f * Renderer::getScreenHeightModifier())};
        mRenderer->drawRect(entry.pos.x, entry.pos.y, entry.size.x, stroke,
                            MadTheme::color(MadColor::HighlightAccent), MadTheme::color(MadColor::HighlightAccent));
        mRenderer->drawRect(entry.pos.x, entry.pos.y + entry.size.y - stroke, entry.size.x,
                            stroke, MadTheme::color(MadColor::HighlightAccent), MadTheme::color(MadColor::HighlightAccent));
        mRenderer->drawRect(entry.pos.x, entry.pos.y, stroke, entry.size.y,
                            MadTheme::color(MadColor::HighlightAccent), MadTheme::color(MadColor::HighlightAccent));
        mRenderer->drawRect(entry.pos.x + entry.size.x - stroke, entry.pos.y, stroke,
                            entry.size.y, MadTheme::color(MadColor::HighlightAccent), MadTheme::color(MadColor::HighlightAccent));
    }

    for (const Entry& entry : mEntries)
        entry.text->render(trans);
}

std::vector<HelpPrompt> MadChipRow::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    prompts.push_back(HelpPrompt("left/right", "choose"));
    prompts.push_back(HelpPrompt("a", "toggle"));
    return prompts;
}
