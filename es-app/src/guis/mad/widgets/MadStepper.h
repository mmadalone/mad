//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadStepper.h
//
//  ‹ value › stepper row for a float range (MAD control panel, deck-patches).
//  Focus is driven by the owning page (onFocusGained/Lost), input is forwarded
//  the same way MadTileGrid is wired; holding left/right repeats via update().
//

#ifndef ES_APP_GUIS_MAD_WIDGETS_MAD_STEPPER_H
#define ES_APP_GUIS_MAD_WIDGETS_MAD_STEPPER_H

#include "components/TextComponent.h"
#include "renderers/Renderer.h"

#include <functional>
#include <memory>
#include <string>

class MadStepper : public GuiComponent
{
public:
    MadStepper(const std::string& label,
               const float minValue,
               const float maxValue,
               const float step,
               const std::function<std::string(float)>& format,
               const std::function<void(float)>& onChange);

    // Named value()/setValue(float) to steer clear of GuiComponent's virtual
    // std::string getValue()/setValue(std::string) pair.
    float value() const { return mValue; }
    void setValue(const float value);

    bool input(InputConfig* config, Input input) override;
    void update(int deltaTime) override;
    void render(const glm::mat4& parentTrans) override;
    void onSizeChanged() override;
    void onFocusGained() override;
    void onFocusLost() override;

    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void adjust(const int direction);
    void refreshValueText();

    Renderer* mRenderer;
    std::shared_ptr<TextComponent> mLabel;
    std::shared_ptr<TextComponent> mLeftArrow;
    std::shared_ptr<TextComponent> mValueText;
    std::shared_ptr<TextComponent> mRightArrow;

    std::function<std::string(float)> mFormat;
    std::function<void(float)> mOnChange;

    float mMin;
    float mMax;
    float mStep;
    float mValue;

    bool mFocused;
    int mHeldDirection; // -1 / 0 / 1 while left/right is held.
    int mHeldTime; // Accumulated deltaTime since the press, in ms.
    int mNextRepeat; // mHeldTime threshold for the next auto-repeat step.
};

#endif // ES_APP_GUIS_MAD_WIDGETS_MAD_STEPPER_H
