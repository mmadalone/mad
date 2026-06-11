//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePreview.h
//
//  MAD control panel: live routing preview (deck-patches). One preview.all
//  request feeds the whole page: connected controllers (SDL order, with
//  battery), DolphinBar status, the X-Arcade port identity, and the would-route
//  result per routed system/collection. Auto-refreshes on devices.changed.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PREVIEW_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PREVIEW_H

#include "components/ButtonComponent.h"
#include "components/ImageComponent.h"
#include "guis/mad/MadPage.h"
#include "renderers/Renderer.h"

class GuiMadPagePreview : public MadPage
{
public:
    GuiMadPagePreview(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    // Drives the 2-second Wiimote poll: sync/drop is HID-only (no evdev node
    // change), so devices.watch never fires for it. Suspended while a capture
    // stream holds the input lock.
    void update(int deltaTime) override;
    void render(const glm::mat4& parentTrans) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onDevicesChanged(const rapidjson::Value& data) override;

private:
    void requestPreview(const bool force);
    void rebuildBody(const rapidjson::Value& result);
    // Appends one line to the (manually rendered) scrollable body; y is in
    // body space and advances by the row height. An optional icon (controller /
    // DolphinBar / console art, letterboxed into iconWidth x iconHeight) is
    // placed left of the text; the row grows to fit it. Empty path = text only.
    void addBodyLine(const float x,
                     float& y,
                     const float width,
                     const std::string& text,
                     const unsigned int color,
                     const std::string& iconPath = "",
                     const float iconWidth = 0.0f,
                     const float iconHeight = 0.0f);
    void applyTopFocus();
    void identifyXarcade();
    void clearXarcade();
    // Forced devices.wiimotes probe; on a count/presence change it patches the
    // DolphinBar line in place and re-previews (non-forced) for the route lines.
    void pollWiimotes();
    // Writes the DolphinBar status line from mWiiPresent/mWiiSlots/mWiiCount.
    void applyDolphinLine();

    Renderer* mRenderer;
    std::vector<std::shared_ptr<ButtonComponent>> mTopButtons;
    std::shared_ptr<TextComponent> mXaStatus;
    // Body lines live in body space and render through a clip rect + scroll
    // offset; the focusable top row is never rebuilt, so focus survives the
    // per-response body rebuilds.
    std::vector<std::shared_ptr<TextComponent>> mBodyLines;
    // Non-focusable row icons (controller / DolphinBar / console art), rebuilt
    // with the body; left-center anchored (origin {0, 0.5}).
    std::vector<std::shared_ptr<ImageComponent>> mBodyImages;
    // The DolphinBar status line within mBodyLines, patched in place by the
    // Wiimote poll (rebuilt with the rest of the body on every preview).
    std::shared_ptr<TextComponent> mDolphinLine;

    int mTopFocus;
    float mBodyTop; // Page-relative y where the scrollable body starts.
    float mBodyHeight; // Content height of the body (for scroll clamping).
    float mScrollOffset;
    bool mRequestInFlight;
    bool mRefreshPending;
    // A forced refresh requested while another was in flight must stay forced
    // when the queued re-request finally goes out.
    bool mPendingForce;
    // Last known DolphinBar state (from preview.all or the poll).
    bool mWiiPresent;
    int mWiiSlots;
    int mWiiCount;
    int mWiimotePollTimer; // Accumulated ms toward the next Wiimote poll.
    bool mWiimotePollInFlight;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PREVIEW_H
