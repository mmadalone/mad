//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadFormPage.h
//
//  MAD control panel: the shared scrolling form column (deck-patches). Sixteen
//  pages build their controls through this - it is the panel's general form
//  base, not a lightgun one, which is why it no longer lives in that page's
//  header.
//

#ifndef ES_APP_GUIS_MAD_MAD_FORM_PAGE_H
#define ES_APP_GUIS_MAD_MAD_FORM_PAGE_H

#include "components/ButtonComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadChipRow.h"
#include "guis/mad/widgets/MadScrollView.h"
#include "guis/mad/widgets/MadStepper.h"

#include <functional>
#include <string>
#include <vector>

// A MadScrollView column of focusable controls (chips / steppers / buttons)
// with the Backends-detail focus-chain semantics (up/down move, the control
// consumes left/right/A, LT/RT page with focus landing, ltrt help prompt).
class MadFormPage : public MadPage
{
public:
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    void update(int deltaTime) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;

protected:
    MadFormPage(GuiMadPanel* panel, const std::string& title);

    // Relayouts triggered from a widget's OWN callback must be deferred to the
    // next update() tick: a synchronous rebuild would destroy the widget while
    // its input() frame and its std::function are still executing.
    void deferRelayout(const std::function<void()>& relayout) { mDeferred = relayout; }

    struct Control {
        enum class Type { Chips, Stepper, Button };
        Type type;
        GuiComponent* comp;
        float top;
        float bottom;
        int row; // up/down move between rows; left/right within one.
    };

    // Layout helpers operating on mScroll/mY (pages call beginColumn() first).
    void beginColumn();
    void endColumn();
    std::shared_ptr<TextComponent> addBlock(const std::string& text, const float fontSize,
                                            const unsigned int color, const float padAfter);
    void header(const std::string& label);
    void caption(const std::string& help);
    std::shared_ptr<MadChipRow> addChips(const std::vector<MadChipRow::Chip>& chips,
                                         const bool momentary);
    std::shared_ptr<MadStepper> addStepper(
        const std::string& label, const float lo, const float hi, const float step,
        const std::function<std::string(float)>& format,
        const std::function<void(float)>& onChange, const float initial,
        const float widthFraction = 0.45f, const float valueWidthFraction = 0.22f);
    std::shared_ptr<ButtonComponent> addButton(const std::string& text,
                                               const std::function<void()>& callback);
    // Several buttons flowing left-to-right on one focus row (wraps onto
    // extra lines when the column is too narrow): left/right walk the row,
    // up/down leave it. Uses the screen width instead of stacking.
    std::vector<std::shared_ptr<ButtonComponent>> addButtonRow(
        const std::vector<std::pair<std::string, std::function<void()>>>& items,
        const bool upperCase = true);
    // Shift every control from index `fromIndex` down by `deltaY` (focus rects
    // included) — the testers build their button row first to learn its true
    // wrapped height, then push it to the bottom of the viewport so the canvas
    // art gets all the room in between.
    void moveControls(const size_t fromIndex, const float deltaY);
    // Re-pack the X positions of one button row after a label change (e.g.
    // the START↔STOP toggle). Lines keep their Y; assumes the width delta is
    // small enough not to change the wrapping.
    void reflowRow(const int row);
    void clearColumn();
    void setFocus(const int index);
    void followFocus();

    int firstOfRow(const int row) const;
    // The control in `row` whose centre-X is nearest `centerX` (column-aware
    // up/down). For single-control rows this returns that one control, so existing
    // pages navigate identically; multi-control (wrapped) rows get true 4-way nav.
    int nearestOfRow(const int row, const float centerX) const;

    std::shared_ptr<MadScrollView> mScroll;
    std::vector<std::shared_ptr<GuiComponent>> mWidgets;
    std::vector<Control> mControls;
    std::function<void()> mDeferred;
    float mY;
    int mFocus;
    int mFocusCookie2;
    int mNextRow;
    float mScrollCookie;
    bool mBuilt;
};

// Transitional: the class was called MadLightgunPageBase while it lived in the
// lightgun page's header. Drop this alias once no includer names it.
using MadLightgunPageBase = MadFormPage;

#endif // ES_APP_GUIS_MAD_MAD_FORM_PAGE_H
