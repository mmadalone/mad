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

#include "components/ImageComponent.h" // GuiMadPageLightgunCamera's live preview.
#include "guis/mad/MadFormPage.h"

#include <functional>
#include <string>
#include <vector>

class GuiMadPageLightgun : public MadFormPage
{
public:
    GuiMadPageLightgun(GuiMadPanel* panel);
    ~GuiMadPageLightgun();

    void build() override;
    void update(int deltaTime) override;
    void onChildPopped() override {} // Sub-pages save through the daemon; nothing to refresh.

private:
    void rebuild(const rapidjson::Value& result);
    void driverAction(const std::string& action);
    void applySmoother();
    void applyDriverState(const bool running);
    void installDriver(); // sinden.install stream → footer lines.

    // Smoother state (mirrors the daemon truth; steppers update it live).
    float mAlpha;
    float mDeadzone;
    int mSnap;
    std::shared_ptr<MadStepper> mAlphaStepper;
    std::shared_ptr<MadStepper> mDeadzoneStepper;
    std::shared_ptr<MadStepper> mSnapStepper;
    std::shared_ptr<TextComponent> mDriverLine;
    int mStatusPollAccum {0};
    // sinden.health (fetched before the status request in build()).
    bool mHealthDriver {true};
    bool mHealthMono {true};
    std::string mInstallToken;
};

class GuiMadPageLightgunButtons : public MadFormPage
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

class GuiMadPageLightgunBehavior : public MadFormPage
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

class GuiMadPageLightgunCamera : public MadFormPage
{
public:
    GuiMadPageLightgunCamera(GuiMadPanel* panel);
    ~GuiMadPageLightgunCamera();

    void build() override;
    void update(int deltaTime) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    // Buffered X=Save / Y=Cancel. Sliders live-apply to v4l2 as you drag, so
    // Cancel is a backend revert (camera.cancel re-seeds from the saved config
    // and re-applies live); Save persists (camera.save). dirty is a simple flag
    // set on any adjustment.
    bool madSave() override;
    bool madCancel() override;
    bool hasUnsavedEdits() const override { return mDirty; }

private:
    void rebuild(const rapidjson::Value& result);
    void togglePreview(const int player);
    void setCam(const int player, const std::string& ctrl, const int value,
                const bool isAuto = false, const bool autoValue = false);
    void saveCamera(); // shared by the on-screen SAVE button and X=Save
    void pollFrame();

    std::shared_ptr<ImageComponent> mPreview; // Page-level child, right half.
    std::shared_ptr<TextComponent> mPreviewHint;
    std::string mFramePath;
    std::string mStreamToken;
    bool mPreviewLive;
    int mPollAccum;
    long long mLastFrameMtimeNs;
    std::vector<unsigned char> mFrameRgba;
    bool mDirty {false}; // a slider was adjusted since the last save/load
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_LIGHTGUN_H
