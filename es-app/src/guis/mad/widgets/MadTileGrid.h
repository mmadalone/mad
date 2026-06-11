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
        bool badge {false};
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

    // Jumps roughly one viewport per step; direction is -1 or 1.
    void pageScroll(const int direction);

    int cursorIndex() const { return mCursor; }
    void setCursorIndex(const int index);

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
    int rowCount() const;

    Renderer* mRenderer;
    std::vector<TileEntry> mEntries;
    std::function<void(const std::string&)> mOnPick;

    int mCursor;
    int mColumns;
    float mCellWidth;
    float mCellHeight;
    float mArtWidth;
    float mArtHeight;
    float mScrollOffset;
};

#endif // ES_APP_GUIS_MAD_WIDGETS_MAD_TILE_GRID_H
