//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageGamepads.h
//
//  MAD control panel: Gamepad tester (deck-patches). Picker grid of connected
//  supported pads (incl. DolphinBar Wii Remotes) → per-pad test page: the
//  daemon grabs the pad (150 ms delayed) and streams ≤30 Hz sprite snapshots;
//  this page only renders them on a MadSpriteCanvas. Backend-owned escapes
//  (hold Start/+ 6 s; Deck pad idle auto-stop) release the grab even if the
//  panel hangs.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_GAMEPADS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_GAMEPADS_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase.
#include "guis/mad/widgets/MadSpriteCanvas.h"
#include "guis/mad/widgets/MadTileGrid.h"

#include <set>
#include <string>
#include <vector>

class GuiMadPageGamepads : public MadPage
{
public:
    GuiMadPageGamepads(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;

private:
    struct Pad {
        std::string kind; // "pad" | "wii"
        std::string path;
        std::string node;
        int slot {0};
        std::string ext;
        std::string name;
        std::string idtail;
        std::string uniq;
        std::string profileKey;
        std::string profileLabel;
        std::string profileDir;
        std::string iconPath;
    };

    void refreshList();

    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<MadTileGrid> mGrid;
    std::vector<Pad> mPads;
};

class GuiMadPageGamepadTest : public MadLightgunPageBase
{
public:
    GuiMadPageGamepadTest(GuiMadPanel* panel,
                          const std::string& kind, const std::string& path,
                          const std::string& node, const int slot,
                          const std::string& ext, const std::string& name,
                          const std::string& idtail, const std::string& uniq,
                          const std::string& profileKey,
                          const std::string& profileLabel,
                          const std::string& profileDir);
    ~GuiMadPageGamepadTest();

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void update(int deltaTime) override;
    bool onBackPressed() override; // Exits edit/calibrate modes first.

private:
    void rebuild(const rapidjson::Value& layout);
    void buildCanvasItems(MadSpriteCanvas* canvas, const rapidjson::Value& sprites,
                          const rapidjson::Value& positions,
                          const std::vector<std::string>& allowed, const bool p2);
    void startTest();
    void stopTest();
    void onStreamPush(const rapidjson::Value& data);
    void applyWii(const rapidjson::Value& wii);
    void requestExtCanvas(const std::string& kind);
    void toggleEdit();
    void toggleCalibrate();
    void savePositions();
    void toggleP2();

    // Pad identity (from the picker).
    std::string mKind, mPath, mNode, mExt, mName, mIdtail, mUniq;
    std::string mProfileKey, mProfileLabel, mProfileDir;
    int mSlot;

    std::shared_ptr<MadSpriteCanvas> mCanvas;
    std::shared_ptr<MadSpriteCanvas> mExtCanvas;
    std::string mExtKind;
    std::vector<std::string> mStems;
    std::string mStreamToken;
    bool mRunning;
    bool mEditMode;
    bool mCalMode;
    bool mP2;
    // Edit-mode nudge hold-repeat.
    int mNudgeDx, mNudgeDy, mNudgeAccum;
    // Wii diff state.
    std::set<std::string> mWiiCore, mWiiExt;
    std::string mWiiStatus;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_GAMEPADS_H
