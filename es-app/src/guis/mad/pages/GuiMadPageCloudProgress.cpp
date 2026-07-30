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

int GuiMadPageCloudProgress::focusedRow() const
{
    for (size_t i {0}; i < mJobs.size(); ++i) {
        if (mJobs[i].id == mFocusId)
            return static_cast<int>(i);
    }
    return -1;
}

void GuiMadPageCloudProgress::clampJobFocus()
{
    // Keep the focus on a job that still exists AND is actually drawn: the strip renders
    // at most kMaxJobRows rows and does not scroll, so a focus beyond that would let the
    // buttons act on a transfer the user cannot see.
    const int visible {std::min(static_cast<int>(mJobs.size()),
                                std::min(kMaxJobRows, static_cast<int>(mBars.size())))};
    const int row {focusedRow()};
    if (row >= 0 && row < visible)
        return;
    mFocusId = visible > 0 ? mJobs[0].id : "";
}

const GuiMadPageCloudProgress::JobRow* GuiMadPageCloudProgress::focusedJob() const
{
    const int row {focusedRow()};
    return row >= 0 ? &mJobs[row] : nullptr;
}

void GuiMadPageCloudProgress::pollJobs()
{
    // The registry's truth about EVERY live transfer (this panel's op, the game-end hook
    // push, a detached survivor). Poll, don't stream: coarse rows are enough for the
    // strip; the focused/adopted op still has its live tail via the root's stream.
    mPollPending = true;
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "transfers.list", nullptr,
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            mPollPending = false;
            if (!ok)
                return; // old daemon / hiccup: the strip just stays empty (single-job look)
            std::vector<JobRow> jobs;
            const rapidjson::Value& arr {MadJson::getMember(payload, "jobs")};
            if (arr.IsArray()) {
                for (const rapidjson::Value& j : arr.GetArray()) {
                    const std::string state {MadJson::getString(j, "state")};
                    if (state != "running" && state != "paused")
                        continue;
                    JobRow row;
                    row.id = MadJson::getString(j, "id");
                    row.title = MadJson::getString(j, "title", "Transfer");
                    row.state = state;
                    row.pausedBy = MadJson::getString(j, "paused_by");
                    row.detached = MadJson::getBool(j, "detached", true);
                    row.pct = MadJson::getInt(j, "pct", 0);
                    row.summary = MadJson::getString(j, "summary");
                    jobs.push_back(std::move(row));
                }
            }
            const bool wasMulti {mJobs.size() > 1};
            mJobs = std::move(jobs);
            clampJobFocus();
            // The "up/down transfer" prompt is conditional on having several jobs, so the
            // footer must be refreshed when that count crosses 1 in either direction.
            if ((mJobs.size() > 1) != wasMulti)
                mPanel->refreshHelpPrompts();
        },
        20000);
}

void GuiMadPageCloudProgress::togglePause()
{
    // With registry jobs in view, act on the FOCUSED one (transfers.*); the legacy
    // single-op methods stay as the fallback when the strip is empty (old daemon).
    const JobRow* job {focusedJob()};
    if (job != nullptr) {
        if (!job->detached) {
            footer()->flash("This transfer can't be paused.", 3000, true);
            return;
        }
        const bool wasPaused {job->state == "paused"};
        const std::string id {job->id};
        mJobs[focusedRow()].state =
            wasPaused ? "running" : "paused"; // optimistic; the poll is authoritative
        if (mProgress != nullptr && mJobs.size() == 1)
            mProgress->paused = !wasPaused;   // single job = the adopted op's status line
        std::weak_ptr<int> alive {pageAlive()};
        pageRequest(
            wasPaused ? "transfers.resume" : "transfers.pause",
            [id](MadJson::Writer& w) {
                w.Key("id");
                w.String(id.c_str(), static_cast<rapidjson::SizeType>(id.length()));
            },
            [this, alive](bool ok, const rapidjson::Value& payload) {
                if (alive.expired())
                    return;
                if (!ok)
                    footer()->flash("Couldn't change the transfer.", 4000, true);
                pollJobs(); // re-sync the strip (and the optimistic flip) from the truth
            });
        return;
    }
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
    // With registry jobs in view the halt targets the FOCUSED job (transfers.*).
    const JobRow* job {focusedJob()};
    if (job != nullptr) {
        const std::string id {job->id};
        const std::string transfersMethod {method == "cloud.stop" ? "transfers.stop"
                                                                  : "transfers.cancel"};
        pageRequest(
            transfersMethod,
            [id](MadJson::Writer& w) {
                w.Key("id");
                w.String(id.c_str(), static_cast<rapidjson::SizeType>(id.length()));
            },
            nullptr);
        mPendingPop = true;
        return;
    }
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

    // Keep the job strip fresh (registry truth): first poll immediately, then every
    // kJobsPollMs; never stack requests.
    mPollTimer += deltaTime;
    if (mPollTimer >= kJobsPollMs && !mPollPending) {
        mPollTimer = 0;
        pollJobs();
    }

    // The strip renders whenever the registry knows a live job AND the adopted op is not
    // the whole story: several jobs, or one job while the adopted op has already finished
    // (otherwise that still-live transfer would be invisible while the buttons act on it).
    const bool multi {mJobs.size() > 1 || (!mJobs.empty() && p.done)};
    const int jobRows {multi ? std::min(static_cast<int>(mJobs.size()),
                                        std::min(kMaxJobRows,
                                                 static_cast<int>(mBars.size())))
                             : 0};
    const JobRow* job {focusedJob()};
    const bool paused {job != nullptr ? job->state == "paused" : p.paused};

    if (mOverall != nullptr)
        mOverall->setFraction(p.overallFrac);
    if (mCaption != nullptr)
        mCaption->setText(multi ? "Transfers (UP/DOWN choose which one the buttons act on)"
                                : "Active transfers");
    if (mStatus != nullptr) {
        if (p.done && !multi)
            mStatus->setText(p.rc == 0 ? "Finished — press B to go back."
                                       : "Failed (exit " + std::to_string(p.rc) +
                                             ") — press B to go back.");
        else if (paused)
            mStatus->setText(job != nullptr && job->pausedBy == "gameplay"
                                 ? "Paused during gameplay — resumes when the game quits."
                                 : "Paused — " + p.overallLabel);
        else if (!p.overallLabel.empty())
            mStatus->setText(p.overallLabel);
        else if (job != nullptr && !job->summary.empty())
            mStatus->setText(job->summary); // no adopted stream (e.g. a hook push): coarse tail
    }
    // Keep the PAUSE/RESUME label in step with the focused job / shared flag; re-pack
    // the row since the label width changes.
    if (mPauseButton != nullptr && paused != mLastPaused) {
        mLastPaused = paused;
        mPauseButton->setText(paused ? "RESUME" : "PAUSE", paused ? "RESUME" : "PAUSE");
        layoutButtons();
    }
    for (int i {0}; i < static_cast<int>(mBars.size()); ++i) {
        const auto& bar {mBars[i]};
        if (i < jobRows) {
            const JobRow& row {mJobs[i]};
            std::string label {(i == focusedRow() ? "> " : "   ") + row.title};
            if (row.state == "paused")
                label += row.pausedBy == "gameplay" ? "   [paused: gameplay]" : "   [paused]";
            else if (!row.summary.empty())
                label += "   " + row.summary;
            bar->setLabel(label);
            bar->setFraction(static_cast<float>(row.pct) / 100.0f);
            bar->setVisible(true);
            continue;
        }
        const int ti {i - jobRows};
        if (ti < static_cast<int>(p.transfers.size())) {
            bar->setLabel(p.transfers[ti].label);
            bar->setFraction(p.transfers[ti].frac);
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
    // UP/DOWN pick the job the buttons act on (only meaningful with several live jobs;
    // LEFT/RIGHT stay on the button row).
    if (mJobs.size() > 1 && config->isMappedLike("up", input)) {
        const int row {focusedRow()};
        if (row > 0) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            mFocusId = mJobs[row - 1].id;
        }
        return true;
    }
    if (mJobs.size() > 1 && config->isMappedLike("down", input)) {
        const int row {focusedRow()};
        // Only within the rows actually drawn (the strip does not scroll).
        const int visible {std::min(static_cast<int>(mJobs.size()),
                                    std::min(kMaxJobRows, static_cast<int>(mBars.size())))};
        if (row >= 0 && row + 1 < visible) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            mFocusId = mJobs[row + 1].id;
        }
        return true;
    }
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
    if (mJobs.size() > 1)
        prompts.push_back(HelpPrompt("up/down", "transfer"));
    if (mButtons.size() > 1)
        prompts.push_back(HelpPrompt("left/right", "choose"));
    prompts.push_back(HelpPrompt("a", "select"));
    prompts.push_back(HelpPrompt("b", "back"));
    return prompts;
}
