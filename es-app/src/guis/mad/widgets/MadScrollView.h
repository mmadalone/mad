//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadScrollView.h
//
//  Whole-content scroll container for MAD control panel pages (deck-patches):
//  the native analog of the Tk app's _scroll() canvas. Children are normal
//  addChild() children positioned in VIEW-LOCAL coordinates (y from 0); the
//  view clips to its own bounds and translates by the scroll offset. The
//  owning page keeps routing input/focus itself and calls ensureVisible()
//  whenever its focus moves.
//

#ifndef ES_APP_GUIS_MAD_WIDGETS_MAD_SCROLL_VIEW_H
#define ES_APP_GUIS_MAD_WIDGETS_MAD_SCROLL_VIEW_H

#include "GuiComponent.h"
#include "renderers/Renderer.h"

#include <memory>

class MadReorderList;

class MadScrollView : public GuiComponent
{
public:
    MadScrollView();

    // deck-patches TOUCH: pages hosting a carry-mode reorder list register it here;
    // while a carry is live, pointerInput() routes every event to that list only (a
    // tap outside it drops the carried row in place), so no sibling control inside
    // the view can act on a half-moved order. Weak: an expired registration just
    // deactivates the routing (pages re-create widgets on rebuilds).
    void setCarryList(const std::shared_ptr<MadReorderList>& list) { mCarryList = list; }

    // The full layout height; clamps the offset (content shrinking under a
    // scrolled view must not strand it past the new bottom).
    void setContentHeight(const float height);
    float contentHeight() const { return mContentHeight; }
    bool overflows() const { return mContentHeight > mSize.y + 0.5f; }

    float scrollOffset() const { return mScrollOffset; }
    void setScrollOffset(const float offset);

    // Tk _ensure_visible: snap the view so [top, bottom] (content coords) is
    // inside it — top-snap first, else bottom-snap. Returns true if it moved.
    bool ensureVisible(const float top, const float bottom);

    // One LT/RT step (±0.85 viewport, matching the Preview page). Returns true
    // if the offset changed.
    bool pageScroll(const int direction);

    // deck-patches TOUCH: drag scrolls the content; taps recurse into the children
    // with the scroll translation applied (mirroring render()).
    bool pointerInput(const PointerEvent& event, const glm::mat4& parentTrans) override;

    void render(const glm::mat4& parentTrans) override;
    void onSizeChanged() override;

private:
    float clampOffset(const float offset) const;

    Renderer* mRenderer;
    std::weak_ptr<MadReorderList> mCarryList; // deck-patches TOUCH
    float mContentHeight;
    float mScrollOffset;
};

#endif // ES_APP_GUIS_MAD_WIDGETS_MAD_SCROLL_VIEW_H
