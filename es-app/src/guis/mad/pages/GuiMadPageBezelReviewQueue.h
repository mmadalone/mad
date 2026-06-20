//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBezelReviewQueue.h
//
//  MAD control panel: per-system fuzzy-bezel REVIEW queue (deck-patches). Opened from the
//  Bezel detail page. bezels.fuzzy_review FIRST auto-wires the confident normalized-equal
//  matches, then this page walks the still-unmatched ROMs one at a time IN PLACE, showing
//  each ROM's difflib-ranked candidate bezels (fetched lazily per-ROM via
//  bezels.fuzzy_candidates) with a live preview — the ES-DE semi-automatic-scraper model.
//  A assigns the focused candidate + advances, X skips + advances, B exits. Picks reuse
//  bezels.assign. Self-scrolling list is the new MadVirtualList (no per-ROM page churn).
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_REVIEW_QUEUE_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_REVIEW_QUEUE_H

#include "components/ImageComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadVirtualList.h"

#include <functional>
#include <string>
#include <vector>

class GuiMadPageBezelReviewQueue : public MadPage
{
public:
    GuiMadPageBezelReviewQueue(GuiMadPanel* panel, const std::string& key,
                               const std::string& label,
                               const std::function<void()>& onChanged = nullptr);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override {}
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct Rom { std::string game; std::string title; };
    struct Cand { std::string name; std::string title; std::string preview; };

    void ensureWidgets();
    void showRom(int idx);  // load + display ranked candidates for mRoms[idx]
    void skipRom();         // X: advance without assigning
    void assignSelected();  // A: assign the focused candidate, then advance
    void updatePreview();
    void finish();          // all reviewed: notify parent + pop this page

    std::string mKey;
    std::string mLabel;
    std::function<void()> mOnChanged;
    std::vector<Rom> mRoms;
    std::vector<Cand> mCands; // current ROM's candidates, parallel to the list rows
    int mIdx {0};
    int mAuto {0};            // count auto-wired by the normalized-equal pass on open
    bool mAssignInFlight {false};
    bool mDone {false};       // all ROMs reviewed — show a done state, let the user press B
    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    std::shared_ptr<ImageComponent> mPreview;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_REVIEW_QUEUE_H
