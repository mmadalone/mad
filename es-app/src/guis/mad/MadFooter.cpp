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
#include "guis/mad/MadTheme.h"

MadFooter::MadFooter()
    : mStickyError {false}
    , mFlashTimeLeft {0}
{
    // Same font as the help prompts (HelpComponent's landscape default).
    mText = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::HelpText),
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
    // Empty → help prompts show through (no bg). With text the prompts are
    // suppressed, so paint an OPAQUE themed background (the active MAD page's
    // frame color); otherwise ES-DE's in-view gamelist shows through the
    // reserved help strip behind the status text.
    if (mShownText.empty())
        return;
    const glm::mat4 trans {parentTrans * getTransform()};
    Renderer* renderer {Renderer::getInstance()};
    renderer->setMatrix(trans);
    renderer->drawRect(0.0f, 0.0f, mSize.x, mSize.y, MadTheme::color(MadColor::Frame),
                       MadTheme::color(MadColor::Frame));
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
    const bool hadText {!mShownText.empty()};
    mShownText = text;
    mText->setColor(error ? MadTheme::color(MadColor::Red) : MadTheme::color(MadColor::HelpText));
    mText->setText(text);
    if (hadText != !text.empty() && mOnVisibilityChanged)
        mOnVisibilityChanged();
}
