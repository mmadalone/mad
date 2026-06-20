//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadVirtualList.h
//
//  Virtualized single-column text list for the MAD control panel (deck-patches).
//  Holds plain-string DATA rows but builds and draws ONLY the on-screen window of
//  rows (a small reused pool of TextComponents), so an ~11k-row bezel-source
//  picker costs the same as a 30-row one. This is ES-DE's long-list technique
//  (TextListComponent windows the render) taken one step further — the DATA is
//  windowed too — implemented with MAD's own in-repo idiom: MadTileGrid's
//  scroll/cull/clip + MadReorderList's single-focusable selector-frame list.
//
//  The owning page routes input/focus to it directly (the page-forwarding
//  pattern, exactly how GuiMadPageGamepads drives its MadTileGrid). The widget
//  self-scrolls: the cursor drives an integer top-row window; there is no outer
//  MadScrollView involved.
//

#ifndef ES_APP_GUIS_MAD_WIDGETS_MAD_VIRTUAL_LIST_H
#define ES_APP_GUIS_MAD_WIDGETS_MAD_VIRTUAL_LIST_H

#include "components/TextComponent.h"
#include "renderers/Renderer.h"

#include <functional>
#include <memory>
#include <string>
#include <vector>

class MadVirtualList : public GuiComponent
{
public:
    struct Row {
        std::string text;
        unsigned int color;
    };

    MadVirtualList();

    // Replace the whole row set. keepCursor clamps the existing cursor into the
    // new range (used after a relabel-driven refresh); false resets to the top
    // (used on a new search filter).
    void setRows(const std::vector<Row>& rows, const bool keepCursor);
    // In-place relabel of ONE row (e.g. a ●/○ toggle) WITHOUT moving the cursor
    // or rebuilding — refreshes the on-screen slot if that row is visible.
    void setRow(const int index, const std::string& text, const unsigned int color);
    void clear();

    int cursor() const { return mCursor; }
    void setCursor(const int index);
    int size() const { return static_cast<int>(mRows.size()); }
    bool overflows() const { return size() > screenCount(); }
    float contentHeight() const { return static_cast<float>(mRows.size()) * mRowHeight; }

    void setOnSelect(const std::function<void(int)>& cb) { mOnSelect = cb; }
    void setOnCursorChanged(const std::function<void(int)>& cb) { mOnCursorChanged = cb; }

    // One LT/RT page step (±screenCount rows).
    void pageScroll(const int direction);

    bool input(InputConfig* config, Input input) override;
    void render(const glm::mat4& parentTrans) override;
    void onSizeChanged() override;
    void onFocusGained() override { mFocused = true; }
    void onFocusLost() override { mFocused = false; }
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    int screenCount() const;
    void keepCursorVisible();
    void moveCursor(const int delta); // input-driven: clamps, follows, sounds, callback
    void rebuildPool();  // (re)allocate the visible-row slot pool for the current size
    void layoutWindow(); // assign visible rows to slots — NEVER called from render()

    Renderer* mRenderer;
    std::shared_ptr<Font> mFont;
    float mRowHeight;
    float mTextInset;
    float mTextVOffset; // vertical offset to centre the line within the row band

    std::vector<Row> mRows;
    int mCursor;
    int mTopRow; // first visible row
    bool mFocused;

    // Reused pool of visible-row text slots (size = screenCount + slack). Each
    // mSlotRow[j] tracks which data row slot j currently displays (-1 = none).
    // The skip guard avoids re-shaping text on in-window cursor moves and on an
    // in-place setRow(); a window scroll still re-shapes the rows that moved —
    // slot→row is index-stable, so a one-row scroll reshapes ~screenCount slots
    // (short rows, cheap; revisit with a row%poolSize ring if it ever judders).
    std::vector<std::shared_ptr<TextComponent>> mSlots;
    std::vector<int> mSlotRow;
    int mActiveSlots; // leading slots that hold a visible row this layout

    std::function<void(int)> mOnSelect;
    std::function<void(int)> mOnCursorChanged;
};

#endif // ES_APP_GUIS_MAD_WIDGETS_MAD_VIRTUAL_LIST_H
