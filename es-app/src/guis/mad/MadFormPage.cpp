//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadFormPage.cpp
//
//  MAD control panel: the shared scrolling form column (deck-patches). Lifted
//  out of GuiMadPageLightgun, where it had been living under a lightgun name
//  while sixteen unrelated pages inherited it.
//

#include "guis/mad/MadFormPage.h"

#include "Sound.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadTheme.h"

#include <algorithm>
#include <cmath>

MadFormPage::MadFormPage(GuiMadPanel* panel, const std::string& title)
    : MadPage {panel, title}
    , mY {0.0f}
    , mFocus {0}
    , mFocusCookie2 {0}
    , mNextRow {0}
    , mScrollCookie {0.0f}
    , mBuilt {false}
{
}

void MadFormPage::clearColumn()
{
    // Children first (dtors self-detach from the live scroll view), THEN the
    // scroll view — removeChild on the wrong parent would dangle.
    mControls.clear();
    mWidgets.clear();
    if (mScroll != nullptr) {
        removeChild(mScroll.get());
        mScroll.reset();
    }
}

void MadFormPage::beginColumn()
{
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
    if (mBuilt)
        mFocusCookie2 = mFocus;
    clearColumn();
    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());
    mY = 0.0f;
    mNextRow = 0;
}

void MadFormPage::endColumn()
{
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    mScroll->setContentHeight(mY + smallHeight * 0.5f);
    mScroll->setScrollOffset(mScrollCookie);
    mBuilt = true;
    if (!mControls.empty()) {
        setFocus(glm::clamp(mFocusCookie2, 0, static_cast<int>(mControls.size()) - 1));
        followFocus();
    }
    mPanel->refreshHelpPrompts();
}

std::shared_ptr<TextComponent> MadFormPage::addBlock(const std::string& text,
                                                              const float fontSize,
                                                              const unsigned int color,
                                                              const float padAfter)
{
    auto component = std::make_shared<TextComponent>(text, Font::get(fontSize), color,
                                                     ALIGN_LEFT, ALIGN_CENTER,
                                                     glm::ivec2 {0, 1});
    component->setPosition(0.0f, mY);
    component->setSize(mScroll->getSize().x, 0.0f); // Autosize within the column.
    mScroll->addChild(component.get());
    mWidgets.emplace_back(component);
    mY += component->getSize().y + padAfter;
    return component;
}

void MadFormPage::header(const std::string& label)
{
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    mY += smallHeight * 0.45f;
    addBlock(label, FONT_SIZE_SMALL, MadTheme::color(MadColor::Title), smallHeight * 0.15f);
}

void MadFormPage::caption(const std::string& help)
{
    if (!help.empty())
        addBlock("    " + help, FONT_SIZE_MINI, MadTheme::color(MadColor::Secondary),
                 Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f);
}

std::shared_ptr<MadChipRow> MadFormPage::addChips(
    const std::vector<MadChipRow::Chip>& chips, const bool momentary)
{
    auto row = std::make_shared<MadChipRow>();
    row->setMomentary(momentary);
    row->setPosition(0.0f, mY);
    row->setSize(mScroll->getSize().x, 1.0f);
    row->setChips(chips);
    row->setSize(mScroll->getSize().x, std::max(1.0f, row->contentHeight()));
    mScroll->addChild(row.get());
    mWidgets.emplace_back(row);
    mControls.push_back(
        {Control::Type::Chips, row.get(), mY, mY + row->getSize().y, mNextRow++});
    mY += row->getSize().y + Font::get(FONT_SIZE_SMALL)->getHeight() * 0.15f;
    return row;
}

std::shared_ptr<MadStepper> MadFormPage::addStepper(
    const std::string& label, const float lo, const float hi, const float step,
    const std::function<std::string(float)>& format,
    const std::function<void(float)>& onChange, const float initial,
    const float widthFraction, const float valueWidthFraction)
{
    auto stepper = std::make_shared<MadStepper>(label, lo, hi, step, format, onChange);
    stepper->setPosition(0.0f, mY);
    stepper->setSize(mScroll->getSize().x * widthFraction,
                     Font::get(FONT_SIZE_MEDIUM)->getHeight() * 1.4f);
    stepper->setValueWidthFraction(valueWidthFraction);
    stepper->setValue(initial);
    mScroll->addChild(stepper.get());
    mWidgets.emplace_back(stepper);
    mControls.push_back({Control::Type::Stepper, stepper.get(), mY,
                         mY + stepper->getSize().y, mNextRow++});
    mY += stepper->getSize().y + Font::get(FONT_SIZE_SMALL)->getHeight() * 0.15f;
    return stepper;
}

std::shared_ptr<ButtonComponent> MadFormPage::addButton(
    const std::string& text, const std::function<void()>& callback)
{
    auto button = std::make_shared<ButtonComponent>(text, text, callback);
    button->setPosition(0.0f, mY);
    mScroll->addChild(button.get());
    mWidgets.emplace_back(button);
    mControls.push_back({Control::Type::Button, button.get(), mY,
                         mY + button->getSize().y, mNextRow++});
    mY += button->getSize().y + Font::get(FONT_SIZE_SMALL)->getHeight() * 0.15f;
    return button;
}

std::vector<std::shared_ptr<ButtonComponent>> MadFormPage::addButtonRow(
    const std::vector<std::pair<std::string, std::function<void()>>>& items,
    const bool upperCase)
{
    std::vector<std::shared_ptr<ButtonComponent>> buttons;
    if (items.empty())
        return buttons;
    const float gap {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.5f};
    int rowId {mNextRow++};
    float x {0.0f};
    float lineHeight {0.0f};
    for (const auto& item : items) {
        auto button = std::make_shared<ButtonComponent>(item.first, item.first, item.second);
        if (!upperCase)
            button->setText(item.first, item.first, false);
        if (x > 0.0f && x + button->getSize().x > mScroll->getSize().x) {
            x = 0.0f; // Wrap onto the next line — a NEW focus row, so up/down moves
            mY += lineHeight + gap * 0.4f; // between lines (true 4-way) instead of
            lineHeight = 0.0f;             // walking the whole row with right.
            rowId = mNextRow++;
        }
        button->setPosition(x, mY);
        mScroll->addChild(button.get());
        mWidgets.emplace_back(button);
        mControls.push_back({Control::Type::Button, button.get(), mY,
                             mY + button->getSize().y, rowId});
        x += button->getSize().x + gap;
        lineHeight = std::max(lineHeight, button->getSize().y);
        buttons.emplace_back(button);
    }
    mY += lineHeight + Font::get(FONT_SIZE_SMALL)->getHeight() * 0.15f;
    return buttons;
}

void MadFormPage::reflowRow(const int row)
{
    const float gap {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.5f};
    float x {0.0f};
    float lastY {-1.0f};
    for (Control& control : mControls) {
        if (control.row != row)
            continue;
        const float y {control.comp->getPosition().y};
        if (lastY >= 0.0f && y != lastY)
            x = 0.0f; // Next wrapped line of the same focus row.
        lastY = y;
        control.comp->setPosition(x, y);
        x += control.comp->getSize().x + gap;
    }
}

void MadFormPage::moveControls(const size_t fromIndex, const float deltaY)
{
    for (size_t i {fromIndex}; i < mControls.size(); ++i) {
        Control& control {mControls[i]};
        control.comp->setPosition(control.comp->getPosition().x,
                                  control.comp->getPosition().y + deltaY);
        control.top += deltaY;
        control.bottom += deltaY;
    }
}

int MadFormPage::firstOfRow(const int row) const
{
    for (size_t i {0}; i < mControls.size(); ++i) {
        if (mControls[i].row == row)
            return static_cast<int>(i);
    }
    return -1;
}

int MadFormPage::nearestOfRow(const int row, const float centerX) const
{
    int best {-1};
    float bestDist {0.0f};
    for (size_t i {0}; i < mControls.size(); ++i) {
        if (mControls[i].row != row)
            continue;
        const float cx {mControls[i].comp->getPosition().x +
                        mControls[i].comp->getSize().x * 0.5f};
        const float d {std::fabs(cx - centerX)};
        if (best < 0 || d < bestDist) {
            best = static_cast<int>(i);
            bestDist = d;
        }
    }
    return best;
}

void MadFormPage::setFocus(const int index)
{
    if (mControls.empty())
        return;
    mFocus = glm::clamp(index, 0, static_cast<int>(mControls.size()) - 1);
    for (size_t i {0}; i < mControls.size(); ++i) {
        if (static_cast<int>(i) == mFocus)
            mControls[i].comp->onFocusGained();
        else
            mControls[i].comp->onFocusLost();
    }
    mPanel->refreshHelpPrompts();
}

void MadFormPage::followFocus()
{
    if (mScroll == nullptr || mControls.empty())
        return;
    const Control& control {mControls[mFocus]};
    mScroll->ensureVisible(mFocus == 0 ? 0.0f : control.top, control.bottom);
}

bool MadFormPage::input(InputConfig* config, Input input)
{
    if (!mBuilt || mControls.empty())
        return false;
    if (mControls[mFocus].comp->input(config, input)) {
        followFocus();
        return true;
    }
    if (input.value == 0)
        return false;
    const int row {mControls[mFocus].row};
    const float curX {mControls[mFocus].comp->getPosition().x +
                      mControls[mFocus].comp->getSize().x * 0.5f};
    if (config->isMappedLike("up", input)) {
        int target {nearestOfRow(row - 1, curX)};       // column-aware: same x on line above
        if (target < 0) {                                // at the top row -> wrap to the last row
            int maxRow {0};
            for (const auto& c : mControls)
                maxRow = std::max(maxRow, c.row);
            target = nearestOfRow(maxRow, curX);
        }
        if (target >= 0 && target != mFocus) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            setFocus(target);
            followFocus();
        }
        return true;
    }
    if (config->isMappedLike("down", input)) {
        int target {nearestOfRow(row + 1, curX)};        // column-aware: same x on line below
        if (target < 0)                                  // at the bottom row -> wrap to the first row
            target = nearestOfRow(0, curX);
        if (target >= 0 && target != mFocus) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            setFocus(target);
            followFocus();
        }
        return true;
    }
    // Left/right walk a multi-button row (chips/steppers consume these
    // themselves before we get here).
    if (config->isMappedLike("left", input)) {
        if (mFocus > 0 && mControls[mFocus - 1].row == row) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            setFocus(mFocus - 1);
            followFocus();
        }
        return true;
    }
    if (config->isMappedLike("right", input)) {
        if (mFocus < static_cast<int>(mControls.size()) - 1 &&
            mControls[mFocus + 1].row == row) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            setFocus(mFocus + 1);
            followFocus();
        }
        return true;
    }
    return false;
}

void MadFormPage::pageScroll(int direction)
{
    if (!mBuilt || mScroll == nullptr || mControls.empty())
        return;
    std::vector<PagedTarget> targets;
    for (size_t i {0}; i < mControls.size(); ++i)
        targets.push_back({static_cast<int>(i), -1, mControls[i].top, mControls[i].bottom});
    bool moved {false};
    if (mScroll->overflows())
        moved = mScroll->pageScroll(direction);
    const float viewTop {mScroll->overflows() ? mScroll->scrollOffset() : 0.0f};
    const float viewBottom {viewTop + (mScroll->overflows() ? mScroll->getSize().y :
                                                              mScroll->contentHeight())};
    const int pick {pickPagedTarget(targets, direction, viewTop, viewBottom)};
    if (pick >= 0) {
        if (targets[pick].id != mFocus) {
            setFocus(targets[pick].id);
            moved = true;
        }
        followFocus();
    }
    if (moved)
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
}

std::vector<HelpPrompt> MadFormPage::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (!mBuilt || mControls.empty())
        return prompts;
    prompts.push_back(HelpPrompt("up/down", "choose"));
    switch (mControls[mFocus].type) {
        case Control::Type::Chips: {
            prompts.push_back(HelpPrompt("left/right", "choose"));
            prompts.push_back(HelpPrompt("a", "toggle"));
            break;
        }
        case Control::Type::Stepper: {
            prompts.push_back(HelpPrompt("left/right", "adjust"));
            break;
        }
        case Control::Type::Button: {
            const int row {mControls[mFocus].row};
            const bool multi {(mFocus > 0 && mControls[mFocus - 1].row == row) ||
                              (mFocus < static_cast<int>(mControls.size()) - 1 &&
                               mControls[mFocus + 1].row == row)};
            if (multi)
                prompts.push_back(HelpPrompt("left/right", "choose"));
            prompts.push_back(HelpPrompt("a", "select"));
            break;
        }
    }
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void MadFormPage::update(int deltaTime)
{
    if (mDeferred) {
        // Run OUTSIDE any widget's input frame (the relayout destroys it).
        const std::function<void()> deferred {std::move(mDeferred)};
        mDeferred = nullptr;
        deferred();
    }
    MadPage::update(deltaTime);
}

void MadFormPage::onSaveFocus()
{
    mFocusCookie2 = mFocus;
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void MadFormPage::onRestoreFocus()
{
    if (!mBuilt)
        return;
    setFocus(mFocusCookie2);
    if (mScroll != nullptr)
        mScroll->setScrollOffset(mScrollCookie);
    followFocus();
}
