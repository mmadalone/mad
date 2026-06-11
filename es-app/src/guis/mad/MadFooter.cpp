//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadFooter.cpp
//
//  Single status line for the MAD control panel, shown above ES-DE's help row
//  (deck-patches).
//

#include "guis/mad/MadFooter.h"

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

void MadFooter::onSizeChanged()
{
    mText->setSize(mSize);
}

void MadFooter::apply(const std::string& text, const bool error)
{
    mText->setColor(error ? mMenuColorRed : mMenuColorSecondary);
    mText->setText(text);
}
