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

std::string madFamilyLabel(const std::string& family)
{
    // See the header: the Wii U Pro pad's family token is its raw BT name
    // "Wii Remote Pro"; display it as "WiiU Pro" (matches the pad's short label).
    if (family == "Wii Remote Pro")
        return "WiiU Pro";
    return family;
}

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

void MadReorderList::setOnToggle(std::function<void(int, int)> cb)
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
            // Priority-RANK tags (#1..#N), not fixed player seats: the list is a priority order and
            // connected pads fill the emulator's real ports top-to-bottom at launch, so a "P1/P2"
            // split under-represented systems with >2 ports (e.g. GameCube's 4). #1 stays highlighted.
            const std::string tag {"#" + std::to_string(i + 1)};
            label = "  " + tag + "   " + madFamilyLabel(mItems[i]);
            color = i == 0 ? MadTheme::color(MadColor::Green) : MadTheme::color(MadColor::Primary);
        }
        else {
            const bool hidden {i < mHidden.size() && mHidden[i]};
            label = "  " + madFamilyLabel(mItems[i]) + (hidden ? "   (hidden)" : "");
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
    // Left/Right cycle the focused row's mode (direction-aware). X is reserved
    // panel-wide for Save on buffered pages, so the toggle moved off it.
    if (!mCarrying && mOnToggle &&
        (config->isMappedLike("right", input) || config->isMappedLike("left", input))) {
        mOnToggle(mCursor, config->isMappedLike("right", input) ? 1 : -1);
        return true;
    }
    return false;
}

// deck-patches TOUCH: end a live carry keeping the current order. Used by the
// carry-owns-the-page guards (MadPage / MadScrollView) when a tap lands outside the
// list: the on-screen order is committed first, so no other control can ever act on
// a half-moved order.
void MadReorderList::dropCarry()
{
    if (!mCarrying)
        return;
    mCarrying = false;
    NavigationSounds::getInstance().playThemeNavigationSound(SELECTSOUND);
    if (mOnPointerChanged)
        mOnPointerChanged(); // LAST action.
}

// deck-patches TOUCH: tap-driven reorder mirroring the carry model in input().
// Tap a row = select it; tap the selected row = lift (or drop) it; while carrying,
// drag one row per row-height or tap the destination row and the carried item
// walks there (repeated adjacent swaps, exactly what up/down would do). Drags while
// NOT carrying fall through so the enclosing MadScrollView scrolls the view.
bool MadReorderList::pointerInput(const PointerEvent& event, const glm::mat4& parentTrans)
{
    if (!isVisible() || mItems.empty() || rowHeight() < 1.0f)
        return false;

    const glm::mat4 trans {parentTrans * getTransform()};
    if (!pointerWithinBounds(event, trans))
        return false;

    // Moves the carried row one step, exactly like a carrying up/down in input().
    auto carryStep = [this](const int direction) {
        const int target {mCursor + direction};
        if (target < 0 || target >= static_cast<int>(mItems.size()))
            return false;
        std::swap(mItems[mCursor], mItems[target]);
        if (mCursor < static_cast<int>(mHidden.size()) &&
            target < static_cast<int>(mHidden.size())) {
            bool a {mHidden[mCursor]}, b {mHidden[target]};
            mHidden[mCursor] = b;
            mHidden[target] = a; // carry the hidden flag with its row
        }
        mCursor = target;
        return true;
    };

    if (event.type == PointerEvent::Type::SCROLL) {
        // Only a carried row consumes drags; plain drags scroll the view.
        if (!mCarrying)
            return false;
        // Fling inertia must never move a carried row: a precise reorder wants
        // finger-tracked steps, not a glide past the intended drop position.
        if (event.fling)
            return true;
        if (event.firstEvent)
            mPointerScrollAccumulator = 0.0f;
        mPointerScrollAccumulator += event.delta.y;
        bool moved {false};
        while (mPointerScrollAccumulator <= -rowHeight()) {
            mPointerScrollAccumulator += rowHeight();
            moved |= carryStep(-1);
        }
        while (mPointerScrollAccumulator >= rowHeight()) {
            mPointerScrollAccumulator -= rowHeight();
            moved |= carryStep(1);
        }
        if (moved) {
            rebuildTexts();
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            if (mOnPointerChanged)
                mOnPointerChanged(); // LAST action (page may re-layout).
        }
        return true;
    }

    const glm::vec2 local {glm::inverse(trans) *
                           glm::vec4 {event.point.x, event.point.y, 0.0f, 1.0f}};
    const int row {static_cast<int>(std::floor(local.y / rowHeight()))};

    // Below the last row: dead zone.
    if (row < 0 || row >= static_cast<int>(mItems.size()))
        return true;

    // A tap on an unfocused list only selects and adopts focus (the page moves its
    // focus target here, driving onFocusGained); a lift requires a focused list so
    // a carry can never start invisibly, without its visuals and help prompts.
    if (!mFocused) {
        mCursor = row;
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        // LAST action: the page's focus move may re-layout.
        if (mOnFocusRequested)
            mOnFocusRequested();
        return true;
    }

    if (row == mCursor) {
        // Lift, or drop: mirrors the "a" handling in input().
        if (!mCarrying) {
            mPreLift = mItems;
            mPreLiftHidden = mHidden;
            mPreLiftCursor = mCursor;
        }
        mCarrying = !mCarrying;
        NavigationSounds::getInstance().playThemeNavigationSound(SELECTSOUND);
        if (mOnPointerChanged)
            mOnPointerChanged(); // LAST action.
        return true;
    }

    if (mCarrying) {
        // Walk the carried row to the tapped destination via adjacent swaps.
        const int direction {row > mCursor ? 1 : -1};
        while (mCursor != row) {
            if (!carryStep(direction))
                break;
        }
        rebuildTexts();
    }
    else {
        mCursor = row;
    }
    NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
    if (mOnPointerChanged)
        mOnPointerChanged(); // LAST action.
    return true;
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
            prompts.push_back(HelpPrompt("left/right", "show/hide"));
    }
    return prompts;
}
