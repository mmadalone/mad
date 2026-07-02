//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRetroArchSystems.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageRetroArchSystems.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageRetroArchGame.h"

GuiMadPageRetroArchSystems::GuiMadPageRetroArchSystems(GuiMadPanel* panel,
                                                       const std::string& title)
    : MadPage {panel, title}
{
}

void GuiMadPageRetroArchSystems::build()
{
    mIntro = std::make_shared<TextComponent>(
        "Pick a system to browse its games and edit per-game settings, input remaps, and "
        "controllers.",
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(mViewportPos.x, mViewportPos.y);
    mIntro->setSize(mViewportSize.x, 0.0f);
    addChild(mIntro.get());

    setLoadingText("Loading systems…");
    pageRequest(
        "ragame.systems", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load systems: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            std::vector<MadTileGrid::Tile> tiles;
            const rapidjson::Value& arr {MadJson::getMember(payload, "systems")};
            if (arr.IsArray()) {
                for (rapidjson::SizeType i {0}; i < arr.Size(); ++i) {
                    const rapidjson::Value& row {arr[i]};
                    MadTileGrid::Tile tile;
                    tile.key = MadJson::getString(row, "name");
                    tile.label = tile.key;
                    tile.sublabel = std::to_string(MadJson::getInt(row, "count", 0)) + " games";
                    tile.artPath = MadJson::getString(row, "art");
                    tiles.emplace_back(tile);
                }
            }
            if (tiles.empty()) {
                setLoadingText("No RetroArch systems with games found.");
                mPanel->refreshHelpPrompts();
                return;
            }
            const float top {mIntro->getPosition().y + mIntro->getSize().y +
                             Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f};
            mGrid = std::make_shared<MadTileGrid>();
            mGrid->setPosition(mViewportPos.x, top);
            mGrid->setSize(mViewportSize.x, mViewportPos.y + mViewportSize.y - top);
            mGrid->setTiles(tiles);
            mGrid->setOnPick([this](const std::string& name) {
                mPanel->pushPage(new GuiMadPageRetroArchGame(mPanel, name));
            });
            mGrid->onFocusGained(); // the only focusable widget on the page
            addChild(mGrid.get());
            mPanel->refreshHelpPrompts();
        },
        10000);
}

bool GuiMadPageRetroArchSystems::input(InputConfig* config, Input input)
{
    if (mGrid != nullptr)
        return mGrid->input(config, input);
    return false;
}

void GuiMadPageRetroArchSystems::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageRetroArchSystems::getHelpPrompts()
{
    if (mGrid != nullptr)
        return mGrid->getHelpPrompts();
    return std::vector<HelpPrompt>();
}

void GuiMadPageRetroArchSystems::onSaveFocus()
{
    if (mGrid != nullptr)
        mFocusCookie = mGrid->cursorIndex();
}

void GuiMadPageRetroArchSystems::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mFocusCookie);
}
