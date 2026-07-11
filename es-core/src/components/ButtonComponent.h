//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  ButtonComponent.h
//
//  Basic button, used as a GUI element and for the virtual keyboard buttons.
//

#ifndef ES_CORE_COMPONENTS_BUTTON_COMPONENT_H
#define ES_CORE_COMPONENTS_BUTTON_COMPONENT_H

#include "GuiComponent.h"
#include "components/NinePatchComponent.h"
#include "components/TextComponent.h"

class ButtonComponent : public GuiComponent
{
public:
    ButtonComponent(const std::string& text = "",
                    const std::string& helpText = "",
                    const std::function<void()>& func = nullptr,
                    bool upperCase = false,
                    bool flatStyle = false);

    void onSizeChanged() override;
    void onFocusGained() override;
    void onFocusLost() override;

    void setText(const std::string& text,
                 const std::string& helpText,
                 bool upperCase = true,
                 bool resize = true);
    const std::string& getText() const { return mText; }

    void setPressedFunc(std::function<void()> f) { mPressedFunc = f; }
    void setEnabled(bool state) override;

    void setOpacity(float opacity) override
    {
        mOpacity = opacity;
        mBox.setOpacity(opacity);
    }

    void setPadding(const glm::vec4 padding);
    glm::vec4 getPadding() { return mPadding; }

    // Override the auto-size floor (default = the width of "DELETE"). Pass 0 so the button hugs
    // its own label instead of centering it in the shared minimum width — used for vertical menu
    // lists where a short label ("Wii") would otherwise render with a large leading gap.
    void setMinWidth(float minWidth);

    void setFlatColorFocused(unsigned int color) { mFlatColorFocused = color; }
    void setFlatColorUnfocused(unsigned int color) { mFlatColorUnfocused = color; }

    const std::function<void()>& getPressedFunc() const { return mPressedFunc; }

    bool input(InputConfig* config, Input input) override;
    void render(const glm::mat4& parentTrans) override;

    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    unsigned int getCurTextColor() const;
    void updateImage();

    Renderer* mRenderer;
    NinePatchComponent mBox;

    std::unique_ptr<TextComponent> mButtonText;
    std::function<void()> mPressedFunc;

    glm::vec4 mPadding;

    std::string mText;
    std::string mHelpText;

    bool mFocused;
    bool mEnabled;
    bool mFlatStyle;

    float mMinWidth;
    unsigned int mTextColorFocused;
    unsigned int mTextColorUnfocused;
    unsigned int mFlatColorFocused;
    unsigned int mFlatColorUnfocused;
};

#endif // ES_CORE_COMPONENTS_BUTTON_COMPONENT_H
