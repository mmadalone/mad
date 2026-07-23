//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageCloudProgress.cpp
//
//  MAD control panel: cloud transfer-progress subpage (deck-patches).
//

#include "guis/mad/pages/GuiMadPageCloudProgress.h"

#include "Sound.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "utils/StringUtil.h"

#include <algorithm>

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

    // Control row at the bottom: PAUSE/RESUME, STOP, CANCEL. Built first so the
    // bars above reserve exactly the room the row needs (its wrapped height).
    auto makeButton = [this](const std::string& label, const std::function<void()>& callback) {
        auto button {std::make_shared<ButtonComponent>(label, label, callback)};
        addChild(button.get());
        mButtons.push_back(button);
        return button;
    };
    mLastPaused = mProgress != nullptr && mProgress->paused;
    mPauseButton = makeButton(mLastPaused ? "RESUME" : "PAUSE", [this] { togglePause(); });
    makeButton("STOP", [this] { fireAndPop("cloud.stop"); });
    makeButton("CANCEL", [this] { fireAndPop("cloud.cancel"); });
    layoutButtons();

    float buttonRowTop {mViewportPos.y + mViewportSize.y};
    for (const auto& button : mButtons)
        buttonRowTop = std::min(buttonRowTop, button->getPosition().y);

    // Bars fill the space between the caption and the control row.
    const float bottom {buttonRowTop - gap};
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
    focusButton(mFocus);
    mPanel->refreshHelpPrompts();
}

void GuiMadPageCloudProgress::layoutButtons()
{
    if (mButtons.empty())
        return;
    const float gap {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.6f};
    float rowHeight {0.0f};
    for (const auto& button : mButtons)
        rowHeight = std::max(rowHeight, button->getSize().y);
    const float top {mViewportPos.y + mViewportSize.y - rowHeight};
    float x {mViewportPos.x};
    for (const auto& button : mButtons) {
        button->setPosition(x, top);
        x += button->getSize().x + gap;
    }
}

void GuiMadPageCloudProgress::focusButton(const int index)
{
    if (mButtons.empty())
        return;
    mFocus = glm::clamp(index, 0, static_cast<int>(mButtons.size()) - 1);
    for (size_t i {0}; i < mButtons.size(); ++i) {
        if (static_cast<int>(i) == mFocus)
            mButtons[i]->onFocusGained();
        else
            mButtons[i]->onFocusLost();
    }
    mPanel->refreshHelpPrompts();
}

void GuiMadPageCloudProgress::togglePause()
{
    if (mProgress == nullptr)
        return;
    const bool wasPaused {mProgress->paused};
    mProgress->paused = !wasPaused; // optimistic flip; the response is authoritative
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(wasPaused ? "cloud.resume" : "cloud.pause", nullptr,
                [this, alive, wasPaused](bool ok, const rapidjson::Value& payload) {
                    if (alive.expired() || mProgress == nullptr)
                        return;
                    mProgress->paused = ok ? MadJson::getBool(payload, "paused", !wasPaused)
                                           : wasPaused; // revert on failure
                    if (!ok)
                        footer()->flash("Couldn't change the transfer.", 4000, true);
                });
}

void GuiMadPageCloudProgress::fireAndPop(const std::string& method)
{
    // Fire the halt and leave the view; the running op's stream (owned by the
    // durable Backup root) reports the end and clears the guard. Pop is deferred
    // to update() so the page isn't destroyed inside this input frame.
    pageRequest(method, nullptr, nullptr);
    mPendingPop = true;
}

void GuiMadPageCloudProgress::update(int deltaTime)
{
    if (mPendingPop) {
        // STOP/CANCEL requested a pop; do it here (outside the input frame) so
        // this page — and its buttons — aren't freed mid-event. Nothing touches
        // members after popPage(): it destroys `this`.
        mPendingPop = false;
        GuiMadPanel* panel {mPanel};
        panel->popPage();
        return;
    }

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
        else if (p.paused)
            mStatus->setText("Paused — " + p.overallLabel);
        else if (!p.overallLabel.empty())
            mStatus->setText(p.overallLabel);
    }
    // Keep the PAUSE/RESUME label in step with the shared flag (the daemon or a
    // response may flip it); re-pack the row since the label width changes.
    if (mPauseButton != nullptr && p.paused != mLastPaused) {
        mLastPaused = p.paused;
        mPauseButton->setText(p.paused ? "RESUME" : "PAUSE", p.paused ? "RESUME" : "PAUSE");
        layoutButtons();
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

bool GuiMadPageCloudProgress::input(InputConfig* config, Input input)
{
    if (mButtons.empty())
        return false;
    if (mButtons[mFocus]->input(config, input)) // A activates the focused control
        return true;
    if (input.value == 0)
        return false;
    if (config->isMappedLike("left", input)) {
        if (mFocus > 0) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            focusButton(mFocus - 1);
        }
        return true;
    }
    if (config->isMappedLike("right", input)) {
        if (mFocus < static_cast<int>(mButtons.size()) - 1) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            focusButton(mFocus + 1);
        }
        return true;
    }
    return false;
}

std::vector<HelpPrompt> GuiMadPageCloudProgress::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (mButtons.size() > 1)
        prompts.push_back(HelpPrompt("left/right", "choose"));
    prompts.push_back(HelpPrompt("a", "select"));
    prompts.push_back(HelpPrompt("b", "back"));
    return prompts;
}
