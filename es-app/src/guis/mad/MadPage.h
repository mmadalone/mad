//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadPage.h
//
//  Abstract base class for MAD control panel pages (deck-patches). The panel
//  owns the pages and drives input/update/render for the top of the per-section
//  page stack.
//

#ifndef ES_APP_GUIS_MAD_MAD_PAGE_H
#define ES_APP_GUIS_MAD_MAD_PAGE_H

#include "components/TextComponent.h"
#include "guis/mad/MadBackend.h"

#include <memory>
#include <string>
#include <vector>

class GuiMadPanel;
class MadFooter;

class MadPage : public GuiComponent
{
public:
    MadPage(GuiMadPanel* panel, const std::string& title);

    // Called by the panel once the page has been positioned and sized.
    virtual void build() = 0;
    // Trigger-driven paging; direction is -1 (left trigger) or 1 (right trigger).
    virtual void pageScroll(int direction) {}
    // Return true to make the panel STOP using the shoulder/trigger buttons for section
    // switch + page scroll, handing them to this page's input() instead (e.g. the X-Arcade
    // tester locks them out while editing positions so a bumper can't drop the user out).
    virtual bool consumesSectionNav() { return false; }
    // First crack at the B button, BEFORE the panel pops the page — return
    // true to consume it (e.g. cancel a reorder carry).
    virtual bool onBackPressed() { return false; }
    // X/Y-driven Save/Cancel, handled by the panel BEFORE it falls through to this
    // page's input(). Return true when consumed (there were unsaved edits to act
    // on); return false to let the panel's fall-through reach input() unchanged,
    // so pages that use X/Y for something else keep working.
    virtual bool madSave() { return false; }
    virtual bool madCancel() { return false; }
    // Raw keyboard events, BEFORE any panel handling. Return true to swallow
    // (the Sinden button-map page consumes ALL keyboard input while open: the
    // driver synthesizes keystrokes from gun presses — they feed the ● dots
    // and must never navigate the panel; Tk parity).
    virtual bool onKeyboardInput(InputConfig* config, Input input) { return false; }
    std::vector<HelpPrompt> getHelpPrompts() override { return std::vector<HelpPrompt>(); }

    // Focus cookies: pages with grids/lists save their cursor when a child page
    // is pushed on top and restore it when popped back.
    virtual void onSaveFocus() {}
    virtual void onRestoreFocus() {}
    // Called by the panel after a child page above this one is popped — pages
    // refresh anything the child may have changed (default: nothing).
    virtual void onChildPopped() {}
    // Hotplug push from the panel-level devices.watch stream; data carries
    // {changed:true, devices:[...]}. Only the current page is notified.
    virtual void onDevicesChanged(const rapidjson::Value& data) {}

    void onSizeChanged() override;
    // Root pages hide the in-page title (the sidebar already shows the
    // section); sub-pages (pickers/details) keep theirs — it carries context
    // the sidebar doesn't (e.g. "QUIT COMBO: SNES").
    void setTitleHidden(const bool hidden);

    // The panel keeps a section's built root page alive and re-shows it
    // instantly on return — but only while the backend's state revision (config
    // / devices / bezels) is unchanged since it was built. The panel stamps the
    // epoch here at build time and compares on reshow; a mismatch rebuilds.
    int builtEpoch() const { return mBuiltEpoch; }
    void setBuiltEpoch(const int epoch) { mBuiltEpoch = epoch; }

protected:
    // Life token for callbacks that pageRequest() can't wrap (e.g. capture
    // modal results delivered after the modal pops): bail out when expired.
    std::weak_ptr<int> pageAlive() const { return mAliveToken; }

    // One pageScroll() focus target for pages that scroll their whole content
    // (MadScrollView): a control — or one grid/slot row, carried in `aux` —
    // that LT/RT paging can land on. Rects are in view-local content coords.
    struct PagedTarget {
        int id;
        int aux;
        float top;
        float bottom;
    };
    // Tk _scroll's pick: among targets whose TOP edge lies inside
    // [viewTop, viewBottom], the lowest on page-down (direction 1) / highest
    // on page-up (-1). Returns an index into `targets`, or -1 when none
    // qualifies — then leave focus alone and let the view stay where it is.
    static int pickPagedTarget(const std::vector<PagedTarget>& targets,
                               const int direction,
                               const float viewTop,
                               const float viewBottom);

    // Backend request whose callback is dropped if this page has been destroyed
    // (pages die on section switches and pops while requests may be in flight).
    // Default 10s (not 4s): at startup the backend is busy (SDL warm ~6s, pool
    // contention), so a first page load can legitimately take several seconds —
    // 4s produced spurious "request timed out" errors. A genuinely dead backend
    // is detected separately/immediately; this only delays surfacing a stuck
    // request. Known-slow calls still pass a larger explicit value.
    void pageRequest(const std::string& method,
                     const MadJson::ParamsWriter& params,
                     const MadBackend::ResponseCallback& callback,
                     const int timeoutMs = 10000);

    // Centered placeholder in the viewport; an empty string removes it.
    void setLoadingText(const std::string& text);

    MadBackend* backend() const;
    MadFooter* footer() const;

    GuiMadPanel* mPanel;
    bool mTitleHidden {false};
    std::shared_ptr<TextComponent> mTitle;
    std::shared_ptr<TextComponent> mLoadingText;
    glm::vec2 mViewportPos; // Content area below the title, relative to the page.
    glm::vec2 mViewportSize;
    int mFocusCookie;
    int mBuiltEpoch {0}; // State-revision epoch when this page was built.

private:
    std::shared_ptr<int> mAliveToken;
};

#endif // ES_APP_GUIS_MAD_MAD_PAGE_H
