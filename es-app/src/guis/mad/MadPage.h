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
    std::vector<HelpPrompt> getHelpPrompts() override { return std::vector<HelpPrompt>(); }

    // Focus cookies: pages with grids/lists save their cursor when a child page
    // is pushed on top and restore it when popped back.
    virtual void onSaveFocus() {}
    virtual void onRestoreFocus() {}
    // Called by the panel after a child page above this one is popped — pages
    // refresh anything the child may have changed (default: nothing).
    virtual void onChildPopped() {}

    void onSizeChanged() override;

protected:
    // Backend request whose callback is dropped if this page has been destroyed
    // (pages die on section switches and pops while requests may be in flight).
    void pageRequest(const std::string& method,
                     const MadJson::ParamsWriter& params,
                     const MadBackend::ResponseCallback& callback,
                     const int timeoutMs = 4000);

    // Centered placeholder in the viewport; an empty string removes it.
    void setLoadingText(const std::string& text);

    MadBackend* backend() const;
    MadFooter* footer() const;

    GuiMadPanel* mPanel;
    std::shared_ptr<TextComponent> mTitle;
    std::shared_ptr<TextComponent> mLoadingText;
    glm::vec2 mViewportPos; // Content area below the title, relative to the page.
    glm::vec2 mViewportSize;
    int mFocusCookie;

private:
    std::shared_ptr<int> mAliveToken;
};

#endif // ES_APP_GUIS_MAD_MAD_PAGE_H
