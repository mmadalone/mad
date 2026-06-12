//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadChipRow.h
//
//  Horizontal row of toggle chips for the MAD control panel (deck-patches):
//  the native form of the Tk class_toggle_row / slot toggles / single bool
//  toggle. Chips wrap onto extra lines when the row is too narrow. Focus is
//  page-driven (onFocusGained/Lost); left/right move the chip cursor, A
//  toggles the focused chip optimistically and fires the callback — the page
//  reverts via setChipState() if the write fails.
//

#ifndef ES_APP_GUIS_MAD_WIDGETS_MAD_CHIP_ROW_H
#define ES_APP_GUIS_MAD_WIDGETS_MAD_CHIP_ROW_H

#include "components/TextComponent.h"
#include "renderers/Renderer.h"

#include <functional>
#include <memory>
#include <string>
#include <vector>

class MadChipRow : public GuiComponent
{
public:
    struct Chip {
        std::string value; // The token reported to the callback (class / slot).
        std::string label;
        bool on {false};
    };

    MadChipRow();

    void setChips(const std::vector<Chip>& chips);
    // Momentary mode: chips are ACTIONS, not states — no ✓/· prefix, A fires
    // the callback (second arg true) without flipping anything (e.g. the
    // smoother preset row).
    void setMomentary(const bool momentary) { mMomentary = momentary; }
    void setOnToggle(const std::function<void(const std::string&, bool)>& callback)
    {
        mOnToggle = callback;
    }
    // Sync a chip to the on-disk truth (write-failure rollback / refresh).
    void setChipState(const std::string& value, const bool on);

    bool input(InputConfig* config, Input input) override;
    void render(const glm::mat4& parentTrans) override;
    void onSizeChanged() override;
    void onFocusGained() override { mFocused = true; }
    void onFocusLost() override { mFocused = false; }

    // Total height of the wrapped chip lines for the given width (call after
    // setSize(width, 1) + setChips — the MadScrollView two-pass idiom).
    float contentHeight() const { return mContentHeight; }

    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct Entry {
        Chip chip;
        std::shared_ptr<TextComponent> text;
        glm::vec2 pos;
        glm::vec2 size;
    };

    void layout();
    void refreshChip(Entry& entry);

    Renderer* mRenderer;
    std::vector<Entry> mEntries;
    std::function<void(const std::string&, bool)> mOnToggle;

    int mCursor;
    bool mFocused;
    bool mMomentary;
    float mContentHeight;
};

#endif // ES_APP_GUIS_MAD_WIDGETS_MAD_CHIP_ROW_H
