//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  SliderComponent.h
//
//  Slider to set value in a predefined range.
//

#ifndef ES_CORE_COMPONENTS_SLIDER_COMPONENT_H
#define ES_CORE_COMPONENTS_SLIDER_COMPONENT_H

#include "GuiComponent.h"
#include "components/ImageComponent.h"
#include "components/TextComponent.h"

class SliderComponent : public GuiComponent
{
public:
    using GuiComponent::getValue;
    using GuiComponent::setValue;

    // Minimum and maximum values, how much to increment each step the knob is moved and
    // an optional unit.
    SliderComponent(float min, float max, float increment, const std::string& suffix = "");

    void setCallback(const std::function<void()>& callbackFunc)
    {
        mChangedValueCallback = callbackFunc;
    }

    void setValue(float value);
    float getValue() { return mValue; }

    bool input(InputConfig* config, Input input) override;
    // deck-patches TOUCH: a tap steps the value toward the tapped side of the knob; a
    // horizontal drag sets the value absolutely, 1:1 with the finger.
    bool pointerInput(const PointerEvent& event, const glm::mat4& parentTrans) override;
    void update(int deltaTime) override;
    void render(const glm::mat4& parentTrans) override;

    void onSizeChanged() override;
    void setOpacity(float opacity) override
    {
        mOpacity = opacity;
        mSliderText->setOpacity(opacity);
    }

    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void onValueChanged();

    Renderer* mRenderer;
    float mMin, mMax;
    float mValue;
    float mSingleIncrement;
    float mMoveRate;
    // deck-patches TOUCH: whether the current gesture claimed the slider (decided on
    // its first event by axis dominance, re-latched every gesture).
    bool mPointerDragging {false};
    float mBarLength;
    float mBarHeight;
    float mBarPosY;
    int mMoveAccumulator;
    glm::vec2 mSliderTextSize;

    ImageComponent mKnob;
    ImageComponent mKnobDisabled;

    std::string mSuffix;
    std::unique_ptr<TextComponent> mSliderText;
    std::function<void()> mChangedValueCallback;
};

#endif // ES_CORE_COMPONENTS_SLIDER_COMPONENT_H
