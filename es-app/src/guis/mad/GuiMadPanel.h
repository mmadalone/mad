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
#include "components/HelpComponent.h"
#include "components/ImageComponent.h"
#include "components/TextComponent.h"
#include "guis/mad/MadBackend.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/MadSidebar.h"
#include "renderers/Renderer.h"

#include <functional>
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
    // Leaving a buffered page with unsaved staged edits: prompt Save / Discard /
    // Keep-editing, then commit (madSave) / revert (madCancel) and run `proceed`
    // only when the action succeeded. Used by the panel on B / section switch AND
    // by a page before a self-initiated navigation that would drop its edits
    // (e.g. opening a sub-page). Covers every buffered page uniformly.
    void promptUnsavedThen(MadPage* page, const std::function<void()>& proceed);
    // Re-fetch sidebar.sections and rebuild the sidebar live (order + visibility) while the
    // user is on the Sidebar page — the Apply path. Persisted changes show at once, no reopen.
    void refreshSidebarLive();
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
    // Re-resolve the themed background image + tint for the active page
    // (MadTheme <background> element); no theme background = flat Frame rect.
    void refreshThemedBackground();
    void switchSection(const int index);
    // The shared "leave this page" action (B, or the dialog's Save/Discard): pop the
    // sub-page, or close the panel when at a section root.
    void backOut();
    // Move the current section's root page into mSavedRoots (saving its focus)
    // so switching back can re-show it instantly; child pages above it are
    // dropped, as before.
    void stashCurrentRoot();
    void requestSidebarIcons();
    // After backend-ready, ask sidebar.sections which rows are visible (capability
    // auto-hide + install.conf overrides) and filter the sidebar to them; RPC
    // error/absent falls back to ALL rows (release-skew safe).
    void requestSidebarVisibility();
    // visibleKeys are in backend (sidebar.sections) order — that drives the sidebar row order.
    // live=false: passive landing rebuild (guarded to the pristine Preview landing).
    // live=true: the user pressed Apply on the Sidebar page -> rebuild in place via applySidebarLive.
    void applySidebarVisibility(const std::vector<std::string>& visibleKeys, bool live = false);
    void applySidebarLive(const std::vector<Section>& filtered);
    void rebuildSidebarWidget();
    void preparePage(MadPage* page);
    MadPage* makeRootPage(const int index);
    MadPage* currentPage() { return mPageStack.empty() ? nullptr : mPageStack.back().get(); }

    Renderer* mRenderer;
    std::unique_ptr<MadBackend> mBackend;
    std::unique_ptr<MadSidebar> mSidebar;
    std::unique_ptr<MadFooter> mFooter;
    BusyComponent mBusy;
    // Our OWN help prompts rendered on the (themed, opaque) help strip. ES-DE
    // draws its help BEFORE the top GUI, so the panel's full-height background
    // covers Window's copy; we render this one on top so the help row sits on
    // the MAD page color instead of the gamelist behind. Re-fed only on change.
    HelpComponent mStripHelp;
    std::string mStripHelpSig;
    ImageComponent mBackgroundImage;
    std::string mBackgroundImagePath;
    std::shared_ptr<TextComponent> mStatusText;
    std::shared_ptr<ButtonComponent> mRetryButton;

    std::vector<Section> mSections;      // the VISIBLE sections (filtered from mAllSections)
    std::vector<Section> mAllSections;   // master list of every section (the unfiltered set)
    int mCurrentSection;
    std::vector<std::unique_ptr<MadPage>> mPageStack;
    // Per-section kept-alive root pages: switching back re-shows the stored page
    // instantly (no rebuild / re-request) while it isn't stale. Sized to
    // mSections; an empty slot means "build fresh on next visit".
    std::vector<std::unique_ptr<MadPage>> mSavedRoots;
    // Backend state-revision epoch = sum of the config/devices/bezels counters,
    // seeded from the hello handshake and updated on each state.rev event. A
    // saved root whose builtEpoch differs is stale and gets rebuilt.
    int mStateEpoch;

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
    // From the input.lock "nav" flag: a TESTER lock (the tested pad is grabbed,
    // no SDL echo) lets OTHER pads navigate (e.g. reach STOP); a CAPTURE lock
    // (Daphne / button detect) does not — its press reaches SDL and must be
    // swallowed. Background-poll suspension still keys on mInputLocked alone.
    bool mInputLockAllowNav;
    std::string mDeviceWatchToken;
    // False until the sidebar widget has been built for the very first time. Gates the
    // one-time UNCONDITIONAL first build in applySidebarVisibility() (bypassing its normal
    // same-order/landing-only guards) so the sidebar is always born in the saved
    // SIDEBAR_ORDER — never the hardcoded default — with no visible reorder snap. See
    // onBackendReady() / requestSidebarVisibility() / applySidebarVisibility().
    bool mSidebarBuilt;
};

#endif // ES_APP_GUIS_MAD_GUI_MAD_PANEL_H
