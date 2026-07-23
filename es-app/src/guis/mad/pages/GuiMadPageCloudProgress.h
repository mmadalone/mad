//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageCloudProgress.h
//
//  MAD control panel: a live transfer-progress subpage for the cloud (MEGA) ops
//  (deck-patches). An overall progress bar plus one bar per active rclone transfer,
//  rendered from a CloudProgress struct the Cloud page owns and fills from the RPC
//  stream. Leaving the page (B) detaches the view; the backup keeps running.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_CLOUD_PROGRESS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_CLOUD_PROGRESS_H

#include "components/ButtonComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadProgressBar.h"

#include <memory>
#include <string>
#include <vector>

// Live transfer state shared between the Cloud page (owns the stream, fills this on the UI
// thread) and this subpage (reads it on the UI thread in update()). No locking needed.
struct CloudProgress {
    bool active {false};
    bool done {false};
    bool paused {false}; // the daemon's op is paused (PAUSE/RESUME on the progress subpage)
    int rc {-1};
    float overallFrac {0.0f};
    std::string overallLabel; // "42%  1.2/2.8 GiB  10 MiB/s  ETA 2m", or a status line
    struct Transfer {
        std::string label;
        float frac {0.0f};
    };
    std::vector<Transfer> transfers;
};

class GuiMadPageCloudProgress : public MadPage
{
public:
    GuiMadPageCloudProgress(GuiMadPanel* panel, const std::string& title,
                            const std::shared_ptr<CloudProgress>& progress);

    void build() override;
    void update(int deltaTime) override;
    bool input(InputConfig* config, Input input) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    static const int kMaxTransferBars {8};

    // Focusable control row at the bottom: PAUSE/RESUME, STOP, CANCEL. They act
    // on the daemon's single active op (no token needed). PAUSE/RESUME flips the
    // shared `paused` flag; STOP/CANCEL fire then pop back (deferred to update()
    // so the page isn't destroyed mid-input).
    void layoutButtons();
    void focusButton(const int index);
    void togglePause();
    void fireAndPop(const std::string& method);

    std::shared_ptr<CloudProgress> mProgress;
    std::shared_ptr<MadProgressBar> mOverall;
    std::shared_ptr<TextComponent> mStatus;
    std::shared_ptr<TextComponent> mCaption;
    std::vector<std::shared_ptr<MadProgressBar>> mBars;

    std::vector<std::shared_ptr<ButtonComponent>> mButtons;
    std::shared_ptr<ButtonComponent> mPauseButton;
    int mFocus {0};
    bool mLastPaused {false}; // last `paused` we rendered onto the PAUSE/RESUME label
    bool mPendingPop {false}; // STOP/CANCEL asked to pop; done on the next update() tick
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_CLOUD_PROGRESS_H
