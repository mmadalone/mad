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

private:
    void rebuild(const rapidjson::Value& layout);
    void startTest();
    void onStreamPush(const rapidjson::Value& data);
    void toggleEdit();
    void toggleCalibrate();
    void togglePreview();
    void savePositions();

    void refreshLiveFooter();

    std::shared_ptr<TextComponent> mModeLine;
    std::shared_ptr<MadSpriteCanvas> mCanvas;
    std::map<std::string, std::string> mSpotLabels;
    std::map<std::string, bool> mPressed;
    std::map<std::string, std::string> mStickState;
    std::string mStreamToken;
    bool mRunning;
    bool mEditMode;
    bool mCalMode;
    bool mPreviewAll;
    int mNudgeDx, mNudgeDy, mNudgeAccum;
    int mModePollAccum;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_XARCADE_H
