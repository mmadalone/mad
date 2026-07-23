//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadProgressBar.cpp
//
//  Progress bar widget for the MAD control panel (deck-patches).
//

#include "guis/mad/widgets/MadProgressBar.h"

#include <algorithm>
#include "guis/mad/MadTheme.h"

MadProgressBar::MadProgressBar()
    : mRenderer {Renderer::getInstance()}
    , mFraction {0.0f}
{
    mLabel = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                             MadTheme::color(MadColor::Title), ALIGN_LEFT,
                                             ALIGN_CENTER, glm::ivec2 {0, 0});
}

void MadProgressBar::setFraction(const float fraction)
{
    mFraction = std::clamp(fraction, 0.0f, 1.0f);
}

void MadProgressBar::setLabel(const std::string& label)
{
    mLabel->setText(label);
}

void MadProgressBar::onSizeChanged()
{
    // Label inset a touch from the left edge, vertically centered over the bar.
    mLabel->setPosition(mSize.y * 0.5f, 0.0f);
    mLabel->setSize(mSize.x - mSize.y, mSize.y);
}

void MadProgressBar::render(const glm::mat4& parentTrans)
{
    if (!isVisible() || mSize.x <= 0.0f || mSize.y <= 0.0f)
        return;

    glm::mat4 trans {parentTrans * getTransform()};
    const float radius {mSize.y * 0.5f};
    const Renderer::BlendFactor srcB {Renderer::BlendFactor::SRC_ALPHA};
    const Renderer::BlendFactor dstB {Renderer::BlendFactor::ONE_MINUS_SRC_ALPHA};

    // The rounded-corner shader anchors its SDF to the vertex origin, so each rounded rect
    // MUST be drawn at local (0,0) with its position folded into the matrix (see MadChipRow).
    // Track (dimmed panel).
    mRenderer->setMatrix(trans);
    mRenderer->drawRect(0.0f, 0.0f, mSize.x, mSize.y, MadTheme::color(MadColor::PanelDimmed),
                        MadTheme::color(MadColor::PanelDimmed), false, 1.0f, 1.0f, srcB, dstB,
                        radius);
    // Fill (green), proportional to the fraction.
    const float fillW {mSize.x * mFraction};
    if (fillW > 1.0f) {
        const unsigned int c {MadTheme::color(MadColor::Green)};
        mRenderer->drawRect(0.0f, 0.0f, fillW, mSize.y, c, c, false, 1.0f, 1.0f, srcB, dstB,
                            std::min(radius, fillW * 0.5f));
    }

    mRenderer->setMatrix(trans); // the rounded draws leave the matrix as-is; restore for text
    mLabel->render(trans);
}
