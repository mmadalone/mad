//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageXArcade.h
//
//  MAD control panel: X-Arcade tester (deck-patches). The daemon grabs the
//  cab's GAMEPAD nodes (the trackball stays free so the Deck cursor lives)
//  and streams sprite snapshots onto the cabinet overlay; P1+P2 Start held
//  3 s ends the test backend-side.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_XARCADE_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_XARCADE_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase.
#include "guis/mad/widgets/MadSpriteCanvas.h"

#include <string>
#include <vector>

class GuiMadPageXArcade : public MadLightgunPageBase
{
public:
    GuiMadPageXArcade(GuiMadPanel* panel);
    ~GuiMadPageXArcade();

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void update(int deltaTime) override;
    bool onBackPressed() override;
    // Lock out bumper/trigger section-nav while editing/calibrating so a stray press can't
    // drop the user out of the cabinet-side positioning flow.
    bool consumesSectionNav() override { return mEditMode || mCalMode; }

private:
    void rebuild(const rapidjson::Value& layout);
    void startTest();
    void applyRunState(); // START TEST ↔ STOP TEST toggle label.
    void onStreamPush(const rapidjson::Value& data);
    void toggleEdit();
    void toggleCalibrate();
    void togglePreview();
    void savePositions();

    void refreshLiveFooter();

    std::shared_ptr<TextComponent> mModeLine;
    std::shared_ptr<MadSpriteCanvas> mCanvas;
    std::shared_ptr<ButtonComponent> mStartButton;
    float mStartButtonWidth {0.0f}; // Build-time width (widest label) — pinned.
    std::map<std::string, std::string> mSpotLabels;
    std::map<std::string, bool> mPressed;
    std::map<std::string, std::string> mStickState;
    std::string mStreamToken;
    bool mRunning;
    bool mStartPending {false};    // a tester.start is in flight (mRunning not set true yet)
    bool mEditMode;
    bool mEditStartedTest {false}; // edit auto-started the stream → stop it on edit exit
    bool mCalMode;
    bool mPreviewAll;
    int mNudgeDx, mNudgeDy, mNudgeAccum;
    int mModePollAccum;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_XARCADE_H
