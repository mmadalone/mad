//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadToggleList.h
//
//  A self-contained on/off toggle list for MAD control panel pages
//  (deck-patches). The sibling of MadReorderList: it plugs into a
//  MadScrollView page as ONE focus target (same contract — contentHeight(),
//  cursorRowRect(), onFocusGained/Lost, getHelpPrompts(), and an input() that
//  returns false at the top/bottom edge so the owning page moves focus to the
//  adjacent control). Each row is a label plus a real SwitchComponent glyph;
//  A toggles the focused row and fires setOnToggle(index, key, value). The
//  page applies the write (e.g. an RPC) and, on failure, rolls the row back
//  with setRowValue(). Preferred over an inline ComponentList when the toggles
//  are only one section of a larger scrolling page (ComponentList owns its own
//  camera scroll and would fight the outer MadScrollView).
//

#ifndef ES_APP_GUIS_MAD_WIDGETS_MAD_TOGGLE_LIST_H
#define ES_APP_GUIS_MAD_WIDGETS_MAD_TOGGLE_LIST_H

#include "components/SwitchComponent.h"
#include "components/TextComponent.h"
#include "renderers/Renderer.h"

#include <functional>
#include <memory>
#include <string>
#include <vector>

class MadToggleList : public GuiComponent
{
public:
    struct Item {
        std::string key;   // policy flag / option id passed back to the page
        std::string label; // shown text
        bool value;        // initial on/off state
    };

    MadToggleList();

    void setItems(const std::vector<Item>& items);
    bool empty() const { return mItems.empty(); }
    int size() const { return static_cast<int>(mItems.size()); }

    // Fired when the focused row is toggled with A (state already flipped).
    void setOnToggle(std::function<void(int index, const std::string& key, bool value)> cb);

    // Force a row's displayed state without firing the callback — the page's
    // optimistic-write rollback uses this on RPC failure.
    void setRowValue(int index, bool value);
    bool rowValue(int index) const;
    int rowIndexOfKey(const std::string& key) const; // -1 if absent

    bool input(InputConfig* config, Input input) override;
    void render(const glm::mat4& parentTrans) override;
    void onSizeChanged() override;
    void onFocusGained() override { mFocused = true; }
    void onFocusLost() override { mFocused = false; }

    float contentHeight() const;
    // {top, bottom} of the cursor row in widget-local coords (focus-follow).
    glm::vec2 cursorRowRect() const;
    int cursorIndex() const { return mCursor; }
    void setCursorIndex(int index);

    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void rebuild();
    float rowHeight() const { return mRowHeight; }

    Renderer* mRenderer;
    std::vector<Item> mItems;
    std::vector<std::shared_ptr<TextComponent>> mLabels;
    std::vector<std::shared_ptr<SwitchComponent>> mSwitches;
    std::function<void(int, const std::string&, bool)> mOnToggle;

    int mCursor;
    bool mFocused;
    float mRowHeight;
};

#endif // ES_APP_GUIS_MAD_WIDGETS_MAD_TOGGLE_LIST_H
