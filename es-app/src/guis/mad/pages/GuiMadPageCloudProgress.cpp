//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageCloudProgress.cpp
//
//  MAD control panel: cloud transfer-progress subpage (deck-patches).
//

#include "guis/mad/pages/GuiMadPageCloudProgress.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadTheme.h"
#include "utils/StringUtil.h"

GuiMadPageCloudProgress::GuiMadPageCloudProgress(GuiMadPanel* panel, const std::string& title,
                                                 const std::shared_ptr<CloudProgress>& progress)
    : MadPage {panel, Utils::String::toUpper(title)}
    , mProgress {progress}
{
}

void GuiMadPageCloudProgress::build()
{
    const float fontH {Font::get(FONT_SIZE_SMALL)->getHeight()};
    const float barH {fontH * 1.6f};
    const float gap {fontH * 0.6f};
    float y {mViewportPos.y};

    mOverall = std::make_shared<MadProgressBar>();
    mOverall->setPosition(mViewportPos.x, y);
    mOverall->setSize(mViewportSize.x, barH);
    addChild(mOverall.get());
    y += barH + gap * 0.5f;

    mStatus = std::make_shared<TextComponent>("Starting…", Font::get(FONT_SIZE_SMALL),
                                              MadTheme::color(MadColor::Secondary), ALIGN_LEFT,
                                              ALIGN_CENTER, glm::ivec2 {0, 1});
    mStatus->setPosition(mViewportPos.x, y);
    mStatus->setSize(mViewportSize.x, fontH * 1.4f);
    addChild(mStatus.get());
    y += fontH * 1.4f + gap;

    mCaption = std::make_shared<TextComponent>("Active transfers", Font::get(FONT_SIZE_MINI),
                                               MadTheme::color(MadColor::Title), ALIGN_LEFT,
                                               ALIGN_CENTER, glm::ivec2 {0, 1});
    mCaption->setPosition(mViewportPos.x, y);
    mCaption->setSize(mViewportSize.x, fontH);
    addChild(mCaption.get());
    y += fontH + gap * 0.4f;

    const float bottom {mViewportPos.y + mViewportSize.y};
    for (int i {0}; i < kMaxTransferBars; ++i) {
        if (y + barH * 0.85f > bottom) // only create bars that FIT the viewport (no overrun)
            break;
        auto bar {std::make_shared<MadProgressBar>()};
        bar->setPosition(mViewportPos.x, y);
        bar->setSize(mViewportSize.x, barH * 0.85f);
        bar->setVisible(false);
        addChild(bar.get());
        mBars.push_back(bar);
        y += barH * 0.85f + gap * 0.4f;
    }
    mPanel->refreshHelpPrompts();
}

void GuiMadPageCloudProgress::update(int deltaTime)
{
    MadPage::update(deltaTime);
    if (mProgress == nullptr)
        return;
    const CloudProgress& p {*mProgress};

    if (mOverall != nullptr)
        mOverall->setFraction(p.overallFrac);
    if (mStatus != nullptr) {
        if (p.done)
            mStatus->setText(p.rc == 0 ? "Finished — press B to go back."
                                       : "Failed (exit " + std::to_string(p.rc) +
                                             ") — press B to go back.");
        else if (!p.overallLabel.empty())
            mStatus->setText(p.overallLabel);
    }
    for (int i {0}; i < static_cast<int>(mBars.size()); ++i) {
        const auto& bar {mBars[i]};
        if (i < static_cast<int>(p.transfers.size())) {
            bar->setLabel(p.transfers[i].label);
            bar->setFraction(p.transfers[i].frac);
            bar->setVisible(true);
        }
        else {
            bar->setVisible(false);
        }
    }
}

std::vector<HelpPrompt> GuiMadPageCloudProgress::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    prompts.push_back(HelpPrompt("b", "back"));
    return prompts;
}
