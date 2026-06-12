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

#include <memory>
#include <string>
#include <vector>

class MadReorderList : public GuiComponent
{
public:
    MadReorderList();

    void setItems(const std::vector<std::string>& items);
    std::vector<std::string> items() const;

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
    std::vector<std::shared_ptr<TextComponent>> mTexts;

    int mCursor;
    int mPreLiftCursor;
    bool mCarrying;
    bool mFocused;
};

#endif // ES_APP_GUIS_MAD_WIDGETS_MAD_REORDER_LIST_H
