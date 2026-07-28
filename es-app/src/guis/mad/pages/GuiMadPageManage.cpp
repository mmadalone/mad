//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageManage.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageManage.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageManageSets.h"
#include "guis/mad/widgets/MadTileGrid.h"

#include <algorithm>

namespace
{
    constexpr const char* kAllSentinel {"\x01""all"}; // the leading "All backups" grid tile

    struct CatDef {
        const char* key;
        const char* label;
        const char* icon;
    };
    // Order = the tile order after the "All" tile. Icons reuse each category's Backup-landing icon.
    const CatDef kCats[] {
        {"games", "Games", "per-game"},
        {"emucfg", "Emulator config", "backup-emu"},
        {"esde", "ES-DE settings", "backup-settings"},
        {"bios", "BIOS", "backup-bios"},
        {"system", "System", "backup-system"},
    };
    constexpr int kNumCats {5};

    int catIndex(const std::string& key)
    {
        for (int i {0}; i < kNumCats; ++i)
            if (key == kCats[i].key)
                return i;
        return -1;
    }
}

GuiMadPageManage::GuiMadPageManage(GuiMadPanel* panel)
    : MadPage {panel, "MANAGE BACKUPS"}
    , mCounts(kNumCats, 0)
{
}

void GuiMadPageManage::build()
{
    setLoadingText("Loading backups…");
    fetchCounts();
}

void GuiMadPageManage::fetchCounts()
{
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "manage.list", [](MadJson::Writer&) {},
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            setLoadingText("");
            std::fill(mCounts.begin(), mCounts.end(), 0);
            mTotal = 0;
            if (ok) {
                for (const char* key : {"local", "cloud"}) {
                    const rapidjson::Value& arr {MadJson::getMember(payload, key)};
                    if (!arr.IsArray())
                        continue;
                    for (const rapidjson::Value& r : arr.GetArray()) {
                        if (MadJson::getString(r, "id").empty())
                            continue; // match the set list, which skips id-less rows (defensive)
                        const int idx {catIndex(MadJson::getString(r, "category"))};
                        if (idx >= 0)
                            ++mCounts[idx];
                        ++mTotal;
                    }
                }
            }
            mLoaded = true;
            rebuild();
        },
        20000);
}

void GuiMadPageManage::rebuild()
{
    if (mGrid != nullptr) {
        mGridCookie = mGrid->cursorIndex();
        removeChild(mGrid.get());
        mGrid.reset();
    }
    auto sub = [](int n) {
        return std::to_string(n) + (n == 1 ? " backup" : " backups");
    };
    std::vector<MadTileGrid::Tile> tiles;
    {   // the leading "All" tile: manage EVERY backup set in one list (and bulk-delete everything).
        MadTileGrid::Tile all;
        all.key = kAllSentinel;
        all.label = "All";
        all.sublabel = sub(mTotal);
        all.artPath = MadTheme::routerIconPath("backup-manage");
        tiles.push_back(all);
    }
    for (int i {0}; i < kNumCats; ++i) {
        MadTileGrid::Tile t;
        t.key = kCats[i].key;
        t.label = kCats[i].label;
        t.sublabel = sub(mCounts[i]);
        t.artPath = MadTheme::routerIconPath(kCats[i].icon);
        tiles.emplace_back(t);
    }
    mGrid = std::make_shared<MadTileGrid>();
    mGrid->setPosition(mViewportPos.x, mViewportPos.y);
    mGrid->setSize(mViewportSize.x, mViewportSize.y);
    mGrid->setTiles(tiles);
    mGrid->setCursorIndex(mGridCookie);
    mGrid->setOnPick([this](const std::string& key) { onPick(key); });
    mGrid->onFocusGained();
    addChild(mGrid.get());
    mPanel->refreshHelpPrompts();
}

void GuiMadPageManage::onPick(const std::string& key)
{
    if (key == kAllSentinel) {
        mPanel->pushPage(new GuiMadPageManageSets(mPanel, "all", "All backups"));
        return;
    }
    const int idx {catIndex(key)};
    if (idx < 0)
        return;
    mPanel->pushPage(new GuiMadPageManageSets(mPanel, kCats[idx].key, kCats[idx].label));
}

bool GuiMadPageManage::input(InputConfig* config, Input input)
{
    return mGrid != nullptr && mGrid->input(config, input);
}

void GuiMadPageManage::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageManage::getHelpPrompts()
{
    return mGrid != nullptr ? mGrid->getHelpPrompts() : std::vector<HelpPrompt>();
}

void GuiMadPageManage::onSaveFocus()
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
}

void GuiMadPageManage::onRestoreFocus()
{
    // a delete inside a set list changes the counts -> re-read them on return (rebuild restores the cursor).
    if (mLoaded)
        fetchCounts();
}
