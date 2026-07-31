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
    // constexpr, not `static const int`: these are passed to std::min (which takes
    // const int&), and an in-class-initialized static const int has no out-of-line
    // definition, so that ODR-use fails to link in a Debug build (-O0 does not inline
    // the call away the way -O2 does).
    static constexpr int kMaxTransferBars {8};
    // A queue makes this list longer than it used to get (it was live transfers only,
    // rarely more than one). The strip still does not scroll, so the cap is what the
    // focus clamp honours - raised rather than made scrollable, which would be a bigger change.
    static constexpr int kMaxJobRows {8};
    static constexpr int kJobsPollMs {2000};

    // Focusable control row at the bottom: PAUSE/RESUME, STOP, CANCEL. With ONE live
    // job they act on it (today's look/behavior, unchanged); with several, the top
    // bars become a JOB STRIP (transfers.list) - UP/DOWN picks the job the buttons
    // act on via transfers.* {id}. STOP/CANCEL fire then pop back (deferred to
    // update() so the page isn't destroyed mid-input).
    void layoutButtons();
    void focusButton(const int index);
    void togglePause();
    void fireAndPop(const std::string& method);

    // The multi-job strip: every live registered transfer (registry truth, polled).
    struct JobRow {
        std::string id;
        std::string title;
        std::string state;    // running | paused | queued
        std::string pausedBy; // "" | user | gameplay
        bool detached {true};
        int pct {0};
        int position {0};     // place in the queue (queued rows only; 1 = next to run)
        std::string summary;
    };
    void pollJobs();                 // transfers.list -> mJobs (every kJobsPollMs)
    // The focused job is tracked by ID, not by index: the poll rebuilds the list
    // newest-first, so an index would silently re-target PAUSE/STOP/CANCEL at a
    // different transfer when a job starts or ends between the frame the user saw and
    // their button press. Returns nullptr when nothing is focusable (then the legacy
    // single-op methods act on the daemon's newest op).
    const JobRow* focusedJob() const;
    int focusedRow() const;          // index of mFocusId in mJobs, or -1
    void clampJobFocus();            // keep mFocusId pointing at a live, VISIBLE row

    std::shared_ptr<CloudProgress> mProgress;
    std::shared_ptr<MadProgressBar> mOverall;
    std::shared_ptr<TextComponent> mStatus;
    std::shared_ptr<TextComponent> mCaption;
    std::vector<std::shared_ptr<MadProgressBar>> mBars;

    std::vector<JobRow> mJobs;
    std::string mFocusId;         // the FOCUSED job's id (stable across polls)
    int mPollTimer {kJobsPollMs}; // fire the first poll immediately
    bool mPollPending {false};

    std::vector<std::shared_ptr<ButtonComponent>> mButtons;
    std::shared_ptr<ButtonComponent> mPauseButton;
    int mFocus {0};
    bool mLastPaused {false}; // last `paused` we rendered onto the PAUSE/RESUME label
    bool mPendingPop {false}; // STOP/CANCEL asked to pop; done on the next update() tick
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_CLOUD_PROGRESS_H
