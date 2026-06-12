//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadPlayerSlots.h
//
//  8-slot player-pin editor for the MAD control panel (deck-patches): one SAVE
//  button on top, then "Player N" CELLS in a multi-column grid (title, pin
//  description with ✓/⚠ badge, IDENTIFY / CLEAR buttons) so the wide screen
//  is used. Holds the UNSAVED pin state; setDevices() re-describes without
//  losing it. Scrolls internally (clip-rect render) and keeps the focused
//  cell visible; left/right walk buttons ACROSS columns, up/down move grid
//  rows.
//

#ifndef ES_APP_GUIS_MAD_WIDGETS_MAD_PLAYER_SLOTS_H
#define ES_APP_GUIS_MAD_WIDGETS_MAD_PLAYER_SLOTS_H

#include "components/ButtonComponent.h"
#include "components/TextComponent.h"
#include "renderers/Renderer.h"

#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>

class MadPlayerSlots : public GuiComponent
{
public:
    static constexpr int PLAYER_COUNT {8};

    struct Device {
        std::string name;
        std::string pinId;
    };

    MadPlayerSlots();

    // Replaces the pin state (use for initial load and rebuild-from-truth).
    void setPins(const std::map<int, std::string>& pins);
    // Fresh scan snapshot for descriptions; unsaved pin edits survive.
    void setDevices(const std::vector<Device>& devices);
    // Identify result: a pad can't hold two slots, so any other slot with the
    // same pin is cleared. `name` extends the snapshot if the pad is unknown.
    void assignPin(const int player, const std::string& pinId, const std::string& name);
    const std::map<int, std::string>& pins() const { return mPins; }
    // "name" for a pin + its badge text (for footer hints).
    std::string describePin(const std::string& pinId, std::string& badge) const;

    void setOnIdentify(const std::function<void(int)>& callback) { mOnIdentify = callback; }
    void setOnClear(const std::function<void(int)>& callback) { mOnClear = callback; }
    void setOnSave(const std::function<void(const std::map<int, std::string>&)>& callback)
    {
        mOnSave = callback;
    }

    // Returns false on an unconsumed edge move (up past SAVE / down past
    // Player 8) so the owning page can move focus to an adjacent widget.
    bool input(InputConfig* config, Input input) override;
    void render(const glm::mat4& parentTrans) override;
    void onSizeChanged() override;
    void onFocusGained() override;
    void onFocusLost() override;

    // Focus cookie for the page stack (row index; 0 = SAVE).
    int focusCookie() const { return mFocusRow; }
    void setFocusCookie(const int cookie);
    void focusFirstRow();
    void focusLastRow();

    // Measurement/geometry for use inside a MadScrollView: the editor is sized
    // to its FULL content height there (internal scroll clamps to a no-op) and
    // the page follows the focused cell through the view instead.
    float contentHeight() const
    {
        return mSaveHeight + static_cast<float>(gridRows()) * mRowHeight;
    }
    // {top, bottom} of a focus row's CELL in widget-local coordinates (row 0 =
    // SAVE; players sit in a multi-column grid).
    glm::vec2 rowRect(const int row) const
    {
        if (row == 0)
            return glm::vec2 {0.0f, mSaveHeight};
        const float top {mSaveHeight +
                         static_cast<float>((row - 1) / std::max(1, mColumns)) * mRowHeight};
        return glm::vec2 {top, top + mRowHeight};
    }
    glm::vec2 focusRowRect() const { return rowRect(mFocusRow); }

    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct Row {
        std::shared_ptr<TextComponent> title;
        std::shared_ptr<TextComponent> description;
        std::shared_ptr<ButtonComponent> identify;
        std::shared_ptr<ButtonComponent> clear;
    };

    void layout();
    void refreshDescriptions();
    void applyFocus();
    void keepRowVisible();
    ButtonComponent* focusedButton();

    Renderer* mRenderer;
    std::shared_ptr<ButtonComponent> mSaveButton;
    std::vector<Row> mRows;

    std::map<int, std::string> mPins; // player → pin_id (the UNSAVED state).
    std::vector<Device> mDevices;

    std::function<void(int)> mOnIdentify;
    std::function<void(int)> mOnClear;
    std::function<void(const std::map<int, std::string>&)> mOnSave;

    int gridRows() const
    {
        return (PLAYER_COUNT + std::max(1, mColumns) - 1) / std::max(1, mColumns);
    }

    bool mFocused;
    int mFocusRow; // 0 = SAVE, 1..8 = players.
    int mFocusCol; // 0 = IDENTIFY, 1 = CLEAR (player rows only).
    int mColumns;  // Player cells per grid line.
    float mCellWidth;
    float mSaveHeight;
    float mRowHeight; // ONE grid line of player cells.
    float mScrollOffset;
};

#endif // ES_APP_GUIS_MAD_WIDGETS_MAD_PLAYER_SLOTS_H
