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

namespace
{
    // The HelpComponent's text color — statuses replace the prompts in the
    // same strip and must read as the same UI element.
    constexpr unsigned int HELP_TEXT_COLOR {0x777777FF};
} // namespace

MadFooter::MadFooter()
    : mStickyError {false}
    , mFlashTimeLeft {0}
{
    // Same font as the help prompts (HelpComponent's landscape default).
    mText = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL), HELP_TEXT_COLOR,
                                            ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    addChild(mText.get());
}

void MadFooter::setStatus(const std::string& text, const bool error)
{
    mStickyText = text;
    mStickyError = error;
    // Live truth beats a stale flash: a NEW status cancels an active flash.
    // Clears wait the flash out — the clear-then-flash idiom relies on that.
    if (!text.empty())
        mFlashTimeLeft = 0;
    if (mFlashTimeLeft <= 0)
        apply(mStickyText, mStickyError);
}

void MadFooter::flash(const std::string& text, const int durationMs, const bool error)
{
    mFlashTimeLeft = durationMs;
    apply(text, error);
}

void MadFooter::clear()
{
    mFlashTimeLeft = 0;
    mStickyText.clear();
    mStickyError = false;
    apply("", false);
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
    // No background of our own: the panel suppresses the help prompts while
    // we have text (hasText/onVisibilityChanged), so the status draws on the
    // exact same backdrop the prompts use.
    if (mShownText.empty())
        return;
    renderChildren(parentTrans * getTransform());
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
    const bool hadText {!mShownText.empty()};
    mShownText = text;
    mText->setColor(error ? mMenuColorRed : HELP_TEXT_COLOR);
    mText->setText(text);
    if (hadText != !text.empty() && mOnVisibilityChanged)
        mOnVisibilityChanged();
}
