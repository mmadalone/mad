//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadFooter.cpp
//
//  Dynamic status line for the MAD control panel, living IN ES-DE's help row
//  (deck-patches).
//

#include "guis/mad/MadFooter.h"

#include "renderers/Renderer.h"

MadFooter::MadFooter()
    : mStickyError {false}
    , mFlashTimeLeft {0}
{
    mText = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL), mMenuColorSecondary,
                                            ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    addChild(mText.get());
}

void MadFooter::setStatus(const std::string& text, const bool error)
{
    mStickyText = text;
    mStickyError = error;
    if (mFlashTimeLeft <= 0)
        apply(mStickyText, mStickyError);
}

void MadFooter::flash(const std::string& text, const int durationMs, const bool error)
{
    mFlashTimeLeft = durationMs;
    apply(text, error);
}

void MadFooter::update(int deltaTime)
{
    if (mFlashTimeLeft > 0) {
        mFlashTimeLeft -= deltaTime;
        if (mFlashTimeLeft <= 0) {
            mFlashTimeLeft = 0;
            apply(mStickyText, mStickyError);
        }
    }
    GuiComponent::update(deltaTime);
}

void MadFooter::render(const glm::mat4& parentTrans)
{
    // Empty footer = the strip belongs to ES-DE's help prompts (rendered by
    // Window before the top GUI); with text, cover them so the row is ours.
    if (mShownText.empty())
        return;
    glm::mat4 trans {parentTrans * getTransform()};
    Renderer::getInstance()->setMatrix(trans);
    Renderer::getInstance()->drawRect(0.0f, 0.0f, mSize.x, mSize.y, mMenuColorFrame,
                                      mMenuColorFrame);
    renderChildren(trans);
}

void MadFooter::onSizeChanged()
{
    // Mirror the help prompts' geometry (HelpComponent defaults: x at 1.2% of
    // the screen width, text top at 95.15% of the screen height) so statuses
    // visually replace the prompts rather than floating elsewhere in the strip.
    const float screenHeight {Renderer::getScreenHeight()};
    const float inset {Renderer::getScreenWidth() * 0.012f};
    const float textY {std::max(0.0f, screenHeight * 0.9515f - (screenHeight - mSize.y))};
    mText->setPosition(inset, textY);
    mText->setSize(std::max(0.0f, mSize.x - inset * 2.0f),
                   Font::get(FONT_SIZE_SMALL)->getHeight());
}

void MadFooter::apply(const std::string& text, const bool error)
{
    mShownText = text;
    mText->setColor(error ? mMenuColorRed : mMenuColorSecondary);
    mText->setText(text);
}
