//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadTileGrid.h
//
//  Scrollable grid of console-art tiles for the MAD control panel (deck-patches).
//

#ifndef ES_APP_GUIS_MAD_WIDGETS_MAD_TILE_GRID_H
#define ES_APP_GUIS_MAD_WIDGETS_MAD_TILE_GRID_H

#include "components/ImageComponent.h"
#include "components/TextComponent.h"
#include "renderers/Renderer.h"

#include <functional>
#include <memory>
#include <string>
#include <vector>

class MadTileGrid : public GuiComponent
{
public:
    struct Tile {
        std::string key;
        std::string label;
        std::string sublabel;
        std::string artPath;
        bool badge {false}; // ● locally-configured marker (green sublabel).
        bool warn {false};  // ⚠ problem marker (red sublabel; wins over badge).
    };

    MadTileGrid();

    void setTiles(const std::vector<Tile>& tiles);
    void setOnPick(const std::function<void(const std::string&)>& callback)
    {
        mOnPick = callback;
    }

    bool input(InputConfig* config, Input input) override;
    void render(const glm::mat4& parentTrans) override;
    void onSizeChanged() override;
    // The selector frame renders only while focused — pages with several
    // focusables (Priority root has TWO grids) would otherwise show multiple
    // frames at once. Pages where the grid is the only control focus it once
    // at creation.
    void onFocusGained() override { mFocused = true; }
    void onFocusLost() override { mFocused = false; }

    // Jumps roughly one viewport per step; direction is -1 or 1.
    void pageScroll(const int direction);

    int cursorIndex() const { return mCursor; }
    void setCursorIndex(const int index);

    // Measurement/geometry for use inside a MadScrollView: the grid is sized
    // to its FULL content height there (internal scroll clamps to a no-op) and
    // the page follows the cursor row through the view instead.
    float contentHeight() const { return static_cast<float>(rowCount()) * mCellHeight; }
    int columns() const { return mColumns; }
    int rows() const { return rowCount(); }
    int tileCount() const { return static_cast<int>(mEntries.size()); }
    // {top, bottom} of a row in grid-local coordinates.
    glm::vec2 rowRect(const int row) const
    {
        const float top {static_cast<float>(row) * mCellHeight};
        return glm::vec2 {top, top + mCellHeight};
    }
    glm::vec2 cursorRowRect() const
    {
        return rowRect(mColumns > 0 ? mCursor / mColumns : 0);
    }

    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct TileEntry {
        Tile tile;
        std::shared_ptr<ImageComponent> image;
        std::shared_ptr<TextComponent> label;
        std::shared_ptr<TextComponent> sublabel;
    };

    void layoutTiles();
    void moveCursor(const int amount);
    void keepCursorVisible();
    int rowCount() const
    {
        return mColumns > 0 ?
                   (static_cast<int>(mEntries.size()) + mColumns - 1) / mColumns :
                   0;
    }

    Renderer* mRenderer;
    std::vector<TileEntry> mEntries;
    std::function<void(const std::string&)> mOnPick;

    int mCursor;
    int mColumns;
    bool mFocused;
    float mCellWidth;
    float mCellHeight;
    float mArtWidth;
    float mArtHeight;
    float mScrollOffset;
};

#endif // ES_APP_GUIS_MAD_WIDGETS_MAD_TILE_GRID_H
