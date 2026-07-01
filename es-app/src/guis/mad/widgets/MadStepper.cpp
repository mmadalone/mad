//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadStepper.cpp
//
//  ‹ value › stepper row for a float range (MAD control panel, deck-patches).
//

#include "guis/mad/widgets/MadStepper.h"

#include "Sound.h"

#include <algorithm>
#include <cmath>
#include "guis/mad/MadTheme.h"

namespace
{
    constexpr int INITIAL_REPEAT_DELAY_MS {450};
    constexpr int REPEAT_INTERVAL_MS {120};
} // namespace

MadStepper::MadStepper(const std::string& label,
                       const float minValue,
                       const float maxValue,
                       const float step,
                       const std::function<std::string(float)>& format,
                       const std::function<void(float)>& onChange)
    : mRenderer {Renderer::getInstance()}
    , mFormat {format}
    , mOnChange {onChange}
    , mMin {minValue}
    , mMax {maxValue}
    , mStep {std::max(step, 1e-6f)}
    , mValue {minValue}
    , mFocused {false}
    , mHeldDirection {0}
    , mHeldTime {0}
    , mNextRepeat {0}
{
    mLabel = std::make_shared<TextComponent>(label, Font::get(FONT_SIZE_MEDIUM),
                                             MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
                                             glm::ivec2 {0, 0});
    mLeftArrow = std::make_shared<TextComponent>("‹", Font::get(FONT_SIZE_MEDIUM),
                                                 MadTheme::color(MadColor::Secondary), ALIGN_CENTER, ALIGN_CENTER,
                                                 glm::ivec2 {0, 0});
    mValueText = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_MEDIUM),
                                                 MadTheme::color(MadColor::Primary), ALIGN_CENTER, ALIGN_CENTER,
                                                 glm::ivec2 {0, 0});
    mRightArrow = std::make_shared<TextComponent>("›", Font::get(FONT_SIZE_MEDIUM),
                                                  MadTheme::color(MadColor::Secondary), ALIGN_CENTER, ALIGN_CENTER,
                                                  glm::ivec2 {0, 0});
    addChild(mLabel.get());
    addChild(mLeftArrow.get());
    addChild(mValueText.get());
    addChild(mRightArrow.get());

    refreshValueText();
}

void MadStepper::setValue(const float value)
{
    mValue = glm::clamp(value, mMin, mMax);
    refreshValueText();
}

void MadStepper::setValueWidthFraction(const float frac)
{
    mValueWidthFrac = glm::clamp(frac, 0.05f, 0.9f);
    onSizeChanged();
}

void MadStepper::onSizeChanged()
{
    // Label on the left half; ‹ value › cluster right-aligned in the rest.
    const float arrowWidth {mSize.y * 1.1f};
    const float valueWidth {mSize.x * mValueWidthFrac};
    const float clusterWidth {arrowWidth * 2.0f + valueWidth};

    mLabel->setPosition(0.0f, 0.0f);
    mLabel->setSize(mSize.x - clusterWidth, mSize.y);

    float x {mSize.x - clusterWidth};
    mLeftArrow->setPosition(x, 0.0f);
    mLeftArrow->setSize(arrowWidth, mSize.y);
    x += arrowWidth;
    mValueText->setPosition(x, 0.0f);
    mValueText->setSize(valueWidth, mSize.y);
    x += valueWidth;
    mRightArrow->setPosition(x, 0.0f);
    mRightArrow->setSize(arrowWidth, mSize.y);
}

void MadStepper::adjust(const int direction)
{
    // Step on an integer grid from mMin so repeated float adds can't drift.
    const int maxSteps {static_cast<int>(std::round((mMax - mMin) / mStep))};
    int stepIndex {static_cast<int>(std::round((mValue - mMin) / mStep))};
    stepIndex = glm::clamp(stepIndex + direction, 0, maxSteps);
    const float newValue {mMin + static_cast<float>(stepIndex) * mStep};
    if (newValue == mValue)
        return;
    mValue = newValue;
    refreshValueText();
    NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
    if (mOnChange)
        mOnChange(mValue);
}

void MadStepper::refreshValueText()
{
    mValueText->setText(mFormat ? mFormat(mValue) : std::to_string(mValue));
}

bool MadStepper::input(InputConfig* config, Input input)
{
    // Releases: ANY input matching left OR right unconditionally ends the hold
    // (es-core SliderComponent's pattern). A held RIGHT can be released via a
    // thumbstick event that maps to the LEFT branch — direction-matched
    // clearing would leave mHeldDirection stuck and the repeat running away.
    if (input.value == 0) {
        if (config->isMappedLike("left", input) || config->isMappedLike("right", input)) {
            mHeldDirection = 0;
            return true;
        }
        return false;
    }

    if (config->isMappedLike("left", input)) {
        adjust(-1);
        mHeldDirection = -1;
        mHeldTime = 0;
        mNextRepeat = INITIAL_REPEAT_DELAY_MS;
        return true;
    }
    if (config->isMappedLike("right", input)) {
        adjust(1);
        mHeldDirection = 1;
        mHeldTime = 0;
        mNextRepeat = INITIAL_REPEAT_DELAY_MS;
        return true;
    }
    return false;
}

void MadStepper::update(int deltaTime)
{
    // Hold-to-repeat: accumulate deltaTime past the initial delay, then step
    // at a fixed interval until release (or focus loss).
    if (mHeldDirection != 0) {
        mHeldTime += deltaTime;
        while (mHeldTime >= mNextRepeat) {
            adjust(mHeldDirection);
            mNextRepeat += REPEAT_INTERVAL_MS;
        }
    }
    GuiComponent::update(deltaTime);
}

void MadStepper::onFocusGained()
{
    mFocused = true;
    mLeftArrow->setColor(MadTheme::color(MadColor::Primary));
    mRightArrow->setColor(MadTheme::color(MadColor::Primary));
    mValueText->setColor(MadTheme::color(MadColor::Title));
}

void MadStepper::onFocusLost()
{
    mFocused = false;
    mHeldDirection = 0;
    mLeftArrow->setColor(MadTheme::color(MadColor::Secondary));
    mRightArrow->setColor(MadTheme::color(MadColor::Secondary));
    mValueText->setColor(MadTheme::color(MadColor::Primary));
}

void MadStepper::render(const glm::mat4& parentTrans)
{
    if (!isVisible())
        return;

    glm::mat4 trans {parentTrans * getTransform()};

    if (mFocused) {
        // Flat backdrop behind the ‹ value › cluster, like a focused flat button.
        const float clusterWidth {mSize.y * 2.2f + mSize.x * mValueWidthFrac};
        mRenderer->setMatrix(trans);
        mRenderer->drawRect(mSize.x - clusterWidth, 0.0f, clusterWidth, mSize.y,
                            MadTheme::color(MadColor::Highlight), MadTheme::color(MadColor::Highlight));
    }

    renderChildren(trans);
}

std::vector<HelpPrompt> MadStepper::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    prompts.push_back(HelpPrompt("left/right", "adjust"));
    return prompts;
}
