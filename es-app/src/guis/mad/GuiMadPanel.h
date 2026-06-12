//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPanel.h
//
//  MAD control panel (deck-patches): fullscreen, ES-DE-native shell around the
//  mad-backend.py daemon. Sidebar of sections on the left, a per-section page
//  stack on the right; sections not yet ported launch the classic Tk app.
//

#ifndef ES_APP_GUIS_MAD_GUI_MAD_PANEL_H
#define ES_APP_GUIS_MAD_GUI_MAD_PANEL_H

#include "components/BusyComponent.h"
#include "components/ButtonComponent.h"
#include "components/TextComponent.h"
#include "guis/mad/MadBackend.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/MadSidebar.h"
#include "renderers/Renderer.h"

#include <memory>
#include <string>
#include <vector>

class GuiMadPanel : public GuiComponent
{
public:
    GuiMadPanel();

    bool input(InputConfig* config, Input input) override;
    void update(int deltaTime) override;
    void render(const glm::mat4& parentTrans) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    MadBackend* getBackend() { return mBackend.get(); }
    MadFooter* getFooter() { return mFooter.get(); }
    // True while a capture stream holds the input lock (between input.lock
    // locked:true/false events). Pages use it to suspend background polling.
    bool isInputLocked() const { return mInputLocked; }

    void pushPage(MadPage* page);
    void popPage();
    void refreshHelpPrompts() { updateHelpPrompts(); }
    // Starts (or re-attaches to) the backend's devices.watch hotplug stream
    // and routes its pushes to the current page's onDevicesChanged(). Pages
    // call this from build(); it's idempotent — the backend returns the same
    // stream token with already:true.
    void ensureDeviceWatch();

private:
    enum class PanelState {
        Connecting,
        Ready,
        Errored
    };

    struct Section {
        std::string label;
        std::string artKey; // Label lowercased, spaces → dashes; used for icon lookup.
    };

    void onBackendReady();
    void showConnecting();
    void showError(const std::string& message);
    void switchSection(const int index);
    void requestSidebarIcons();
    void preparePage(MadPage* page);
    MadPage* makeRootPage(const int index);
    MadPage* currentPage() { return mPageStack.empty() ? nullptr : mPageStack.back().get(); }

    Renderer* mRenderer;
    std::unique_ptr<MadBackend> mBackend;
    std::unique_ptr<MadSidebar> mSidebar;
    std::unique_ptr<MadFooter> mFooter;
    BusyComponent mBusy;
    std::shared_ptr<TextComponent> mStatusText;
    std::shared_ptr<ButtonComponent> mRetryButton;

    std::vector<Section> mSections;
    int mCurrentSection;
    std::vector<std::unique_ptr<MadPage>> mPageStack;

    PanelState mPanelState;
    glm::vec2 mContentPos;
    glm::vec2 mContentSize;
    float mSidebarWidth;
    // Bottom strip left to ES-DE's standard underdraw + help-prompt row.
    float mHelpReserve;
    // True between input.lock locked:true/false events (a capture stream is
    // live): the capture modal is window-topmost and handles its own input,
    // but anything that still reaches the panel must be swallowed — the
    // captured press also arrives through SDL.
    bool mInputLocked;
    std::string mDeviceWatchToken;
};

#endif // ES_APP_GUIS_MAD_GUI_MAD_PANEL_H
