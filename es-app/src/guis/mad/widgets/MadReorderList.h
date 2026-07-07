//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadReorderList.h
//
//  Carry-mode reorder list for the MAD control panel Priority editor
//  (deck-patches): rows of controller families tagged P1/P2/#N. A lifts the
//  focused row (carry), up/down then move it, A drops it, B cancels the carry
//  and restores the pre-lift order (the page routes B here via
//  MadPage::onBackPressed before the panel pops the page). Replaces the Tk
//  editor's per-row ↑/↓ buttons with pad-native carry semantics.
//

#ifndef ES_APP_GUIS_MAD_WIDGETS_MAD_REORDER_LIST_H
#define ES_APP_GUIS_MAD_WIDGETS_MAD_REORDER_LIST_H

#include "components/TextComponent.h"
#include "renderers/Renderer.h"

#include <functional>
#include <memory>
#include <string>
#include <vector>

class MadReorderList : public GuiComponent
{
public:
    MadReorderList();

    void setItems(const std::vector<std::string>& items);
    std::vector<std::string> items() const;

    // Optional generalizations — defaults preserve the Priority/PadsPriority look:
    void setPlayerTags(bool on);                      // false: plain rows (no P1/P2/#N, uniform color)
    void setHidden(const std::vector<bool>& hidden);  // per-row dimmed "(hidden)" marker
    void setRowHidden(int index, bool hidden);        // flip one row in place (keeps cursor/carry)
    bool rowHidden(int index) const;
    // Left/Right on the focused row -> cb(cursorIndex, dir) where dir is +1 for
    // Right, -1 for Left (X is reserved panel-wide for Save on buffered pages).
    void setOnToggle(std::function<void(int, int)> cb);

    bool carrying() const { return mCarrying; }
    void cancelCarry(); // Restore the pre-lift order and end the carry.

    bool input(InputConfig* config, Input input) override;
    void render(const glm::mat4& parentTrans) override;
    void onSizeChanged() override;
    void onFocusGained() override { mFocused = true; }
    void onFocusLost() override { mFocused = false; }

    float contentHeight() const;
    // {top, bottom} of the cursor row in widget-local coords (focus-follow).
    glm::vec2 cursorRowRect() const;
    int cursorIndex() const { return mCursor; }

    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void rebuildTexts();
    float rowHeight() const;

    Renderer* mRenderer;
    std::vector<std::string> mItems;
    std::vector<std::string> mPreLift; // Order + cursor snapshot for B-cancel.
    std::vector<bool> mHidden;         // per-row hidden flag (parallel to mItems)
    std::vector<bool> mPreLiftHidden;  // hidden-flag snapshot for B-cancel
    std::function<void(int, int)> mOnToggle; // Left/Right on focused row (unset: ignored)
    std::vector<std::shared_ptr<TextComponent>> mTexts;

    int mCursor;
    int mPreLiftCursor;
    bool mCarrying;
    bool mFocused;
    bool mPlayerTags;                  // true (default): P1/P2/#N tags + green row 0
};

#endif // ES_APP_GUIS_MAD_WIDGETS_MAD_REORDER_LIST_H
