//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageLightgun.h
//
//  MAD control panel: Lightgun / Sinden section (deck-patches). Root page =
//  driver control, smoother tuning, LED strip; sub-pages: P1/P2 button map
//  (with live ● press dots fed from ES-DE's own keyboard events — the driver
//  synthesizes keystrokes from gun presses), P1/P2 recoil & behavior, and the
//  camera tuning page with the live ffmpeg preview (the page polls the frame
//  file and feeds ImageComponent::setRawImage).
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_LIGHTGUN_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_LIGHTGUN_H

#include "components/ButtonComponent.h"
#include "components/ImageComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadChipRow.h"
#include "guis/mad/widgets/MadScrollView.h"
#include "guis/mad/widgets/MadStepper.h"

#include <functional>
#include <map>
#include <string>
#include <vector>

// Shared scaffolding for the lightgun pages: a MadScrollView column of
// focusable controls (chips / steppers / buttons) with the Backends-detail
// focus-chain semantics (up/down move, the control consumes left/right/A,
// LT/RT page with focus landing, ltrt help prompt).
class MadLightgunPageBase : public MadPage
{
public:
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    void update(int deltaTime) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;

protected:
    MadLightgunPageBase(GuiMadPanel* panel, const std::string& title);

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
        const float widthFraction = 0.45f);
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

class GuiMadPageLightgun : public MadLightgunPageBase
{
public:
    GuiMadPageLightgun(GuiMadPanel* panel);

    void build() override;
    void update(int deltaTime) override;
    void onChildPopped() override {} // Sub-pages save through the daemon; nothing to refresh.

private:
    void rebuild(const rapidjson::Value& result);
    void driverAction(const std::string& action);
    void applySmoother();
    void applyDriverState(const bool running);

    // Smoother state (mirrors the daemon truth; steppers update it live).
    float mAlpha;
    float mDeadzone;
    int mSnap;
    std::shared_ptr<MadStepper> mAlphaStepper;
    std::shared_ptr<MadStepper> mDeadzoneStepper;
    std::shared_ptr<MadStepper> mSnapStepper;
    std::shared_ptr<TextComponent> mDriverLine;
    int mStatusPollAccum {0};
};

class GuiMadPageLightgunButtons : public MadLightgunPageBase
{
public:
    GuiMadPageLightgunButtons(GuiMadPanel* panel, const int player);

    void build() override;
    bool onKeyboardInput(InputConfig* config, Input input) override;
    void onChildPopped() override;

private:
    void refresh();
    void rebuild(const rapidjson::Value& result);
    void feedCode(const int code, const bool pressed);

    struct Row {
        std::string base;
        std::string name;
        int code {0};
        int offCode {0};
        int mod {0};
        std::shared_ptr<TextComponent> dot;
    };

    int mPlayer;
    bool mShowOff;
    bool mShowMods;
    std::vector<Row> mRows;
    // Cached picker data (groups flattened to (value, "group: label")).
    std::vector<std::pair<std::string, std::string>> mActionOptions;
    std::vector<std::pair<std::string, std::string>> mModOptions;
    rapidjson::Document mData; // Last sinden.buttons payload (rebuild on toggles).
    bool mHaveData;
};

class GuiMadPageLightgunBehavior : public MadLightgunPageBase
{
public:
    GuiMadPageLightgunBehavior(GuiMadPanel* panel, const int player);

    void build() override;
    void onChildPopped() override; // The handedness pick wrote the config; reload.

private:
    void rebuild(const rapidjson::Value& result);
    void setKey(const std::string& base, const std::string& value);

    int mPlayer;
    std::string mSuffix;
};

class GuiMadPageLightgunCamera : public MadLightgunPageBase
{
public:
    GuiMadPageLightgunCamera(GuiMadPanel* panel);
    ~GuiMadPageLightgunCamera();

    void build() override;
    void update(int deltaTime) override;
    void render(const glm::mat4& parentTrans) override;

private:
    void rebuild(const rapidjson::Value& result);
    void togglePreview(const int player);
    void setCam(const int player, const std::string& ctrl, const int value,
                const bool isAuto = false, const bool autoValue = false);
    void pollFrame();

    std::shared_ptr<ImageComponent> mPreview; // Page-level child, right half.
    std::shared_ptr<TextComponent> mPreviewHint;
    std::string mFramePath;
    std::string mStreamToken;
    bool mPreviewLive;
    int mPollAccum;
    long long mLastFrameMtimeNs;
    std::vector<unsigned char> mFrameRgba;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_LIGHTGUN_H
