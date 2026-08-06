//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMsgBox.h
//
//  Popup message dialog with a notification text and a choice of one,
//  two or three buttons.
//

#ifndef ES_CORE_GUIS_GUI_MSG_BOX_H
#define ES_CORE_GUIS_GUI_MSG_BOX_H

#include "GuiComponent.h"
#include "components/BackgroundComponent.h"
#include "components/ComponentGrid.h"
#include "components/ScrollableContainer.h"
#include "utils/LocalizationUtil.h"

class ButtonComponent;
class TextComponent;

class GuiMsgBox : public GuiComponent
{
public:
    GuiMsgBox(const std::string& text,
              const std::string& name1 = _("OK"),
              const std::function<void()>& func1 = nullptr,
              const std::string& name2 = "",
              const std::function<void()>& func2 = nullptr,
              const std::string& name3 = "",
              const std::function<void()>& func3 = nullptr,
              const std::string& name4 = "",
              const std::function<void()>& func4 = nullptr,
              const std::function<void()>& backFunc = nullptr,
              const bool disableBackButton = false,
              const bool deleteOnButtonPress = true,
              const float maxWidthMultiplier = 0.0f);

    void calculateSize();

    void changeText(const std::string& newText);

private:
    // deck-patches: one layout attempt. Measures the text at its current font, decides the
    // width with the original rules (plus the long-message rule when mLongMessage is set),
    // re-wraps to that width and returns it. Split out so calculateSize() can run it more
    // than once without duplicating the width branches.
    float layoutPass(float aspectValue);
    float measuredMsgHeight() const;

public:

    bool input(InputConfig* config, Input input) override;
    void onSizeChanged() override;

    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void deleteMeAndCall(const std::function<void()>& func);

    Renderer* mRenderer;
    BackgroundComponent mBackground;
    ComponentGrid mGrid;

    std::shared_ptr<TextComponent> mMsg;
    // deck-patches: only created for a LONG message that would otherwise run off the
    // screen. Short confirms keep the original code path exactly.
    std::shared_ptr<ScrollableContainer> mMsgScroll;
    std::vector<std::shared_ptr<ButtonComponent>> mButtons;
    std::shared_ptr<ComponentGrid> mButtonGrid;
    const std::function<void()> mBackFunc;
    bool mDisableBackButton;
    bool mDeleteOnButtonPress;
    // deck-patches: the caller's value is kept separately and never written to.
    // mMaxWidthMultiplier is derived from it on EVERY layout, because a long-message pass
    // raises it - without an immutable source a later changeText() with a short message
    // would inherit the widened value and never return to the normal layout.
    const float mMaxWidthMultiplierRequested;
    float mMaxWidthMultiplier;
    bool mLongMessage {false};
};

#endif // ES_CORE_GUIS_GUI_MSG_BOX_H
