//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBezelProject.cpp
//
//  MAD control panel: Bezel Project page (deck-patches). Tile grid of bezel packs
//  + a per-system install/remove/enable/disable detail page. Mirrors the
//  GuiMadPageStandalones tile-grid pattern; backend = bezels.* (lib/bezel_cfg.py).
//

#include "guis/mad/pages/GuiMadPageBezelProject.h"

#include "Sound.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBezelPerGame.h"

#include <cmath>
#include <functional>
#include <utility>
#include <vector>

//  ── GuiMadPageBezelProject (tile grid) ──

GuiMadPageBezelProject::GuiMadPageBezelProject(GuiMadPanel* panel)
    : MadPage {panel, "BEZEL PROJECT"}
    , mGridCookie {0}
    , mScrollCookie {0.0f}
{
}

void GuiMadPageBezelProject::build()
{
    setLoadingText("Loading bezel packs…");
    pageRequest("bezels.list", nullptr, [this](bool ok, const rapidjson::Value& payload) {
        setLoadingText("");
        if (!ok) {
            footer()->setStatus("Couldn't list bezel packs: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                true);
            return;
        }
        rebuild(payload);
    });
}

void GuiMadPageBezelProject::onChildPopped()
{
    build(); // a detail page may have installed/removed/toggled — refresh status
}

void GuiMadPageBezelProject::rebuild(const rapidjson::Value& result)
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
    mIntro.reset();
    mGrid.reset();
    if (mScroll != nullptr) {
        removeChild(mScroll.get());
        mScroll.reset();
    }
    mLabelByKey.clear();

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};
    mIntro = std::make_shared<TextComponent>(
        "Install or remove The Bezel Project's per-game artwork for RetroArch, per system.",
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(0.0f, y);
    mIntro->setSize(mViewportSize.x, 0.0f);
    mScroll->addChild(mIntro.get());
    y += mIntro->getSize().y + smallHeight * 0.4f;

    std::vector<MadTileGrid::Tile> tiles;
    const rapidjson::Value& arr {MadJson::getMember(result, "systems")};
    if (arr.IsArray()) {
        for (rapidjson::SizeType i {0}; i < arr.Size(); ++i) {
            const rapidjson::Value& row {arr[i]};
            MadTileGrid::Tile tile;
            tile.key = MadJson::getString(row, "key");
            tile.label = MadJson::getString(row, "label", tile.key);
            const bool repo {MadJson::getBool(row, "repo_present", false)};
            const bool installed {MadJson::getBool(row, "installed", false)};
            const int games {MadJson::getInt(row, "games", 0)};
            const int enabled {MadJson::getInt(row, "enabled", 0)};
            if (!repo) {
                tile.sublabel = "not downloaded";
                tile.warn = true;
            }
            else if (!installed) {
                tile.sublabel = "not installed";
            }
            else if (games > 0) {
                tile.sublabel = std::to_string(games) + " games · " + (enabled > 0 ? "on" : "off");
                tile.badge = enabled > 0;
            }
            else {
                tile.sublabel = "installed";
            }
            const rapidjson::Value& art {MadJson::getMember(row, "art")};
            if (art.IsArray() && art.Size() > 0 && art[0].IsString())
                tile.artPath = art[0].GetString();
            tiles.emplace_back(tile);
            mLabelByKey[tile.key] = tile.label;
        }
    }

    if (tiles.empty()) {
        setLoadingText("No bezel packs found.");
    }
    else {
        mGrid = std::make_shared<MadTileGrid>();
        mGrid->setPosition(0.0f, y);
        mGrid->setSize(mViewportSize.x, 1.0f);
        mGrid->setTiles(tiles);
        mGrid->setSize(mViewportSize.x, std::max(1.0f, mGrid->contentHeight()));
        mGrid->setOnPick([this](const std::string& key) {
            const auto it = mLabelByKey.find(key);
            mPanel->pushPage(new GuiMadPageBezelDetail(
                mPanel, key, it != mLabelByKey.end() ? it->second : key));
        });
        mGrid->setCursorIndex(mGridCookie);
        mGrid->onFocusGained();
        mScroll->addChild(mGrid.get());
        y += mGrid->getSize().y;
    }

    mScroll->setContentHeight(y + smallHeight * 0.5f);
    mScroll->setScrollOffset(mScrollCookie);
    followFocus();
    mPanel->refreshHelpPrompts();
}

void GuiMadPageBezelProject::followFocus()
{
    if (mScroll == nullptr || mGrid == nullptr)
        return;
    const glm::vec2 row {mGrid->cursorRowRect()};
    if (mGrid->cursorIndex() / std::max(1, mGrid->columns()) == 0)
        mScroll->ensureVisible(0.0f, mGrid->getPosition().y + row.y);
    else
        mScroll->ensureVisible(mGrid->getPosition().y + row.x, mGrid->getPosition().y + row.y);
}

bool GuiMadPageBezelProject::input(InputConfig* config, Input input)
{
    if (mGrid == nullptr)
        return false;
    if (mGrid->input(config, input)) {
        followFocus();
        return true;
    }
    return false;
}

void GuiMadPageBezelProject::pageScroll(int direction)
{
    if (mScroll == nullptr || mGrid == nullptr)
        return;
    if (!mScroll->overflows()) {
        mGrid->pageScroll(direction);
        followFocus();
        return;
    }
    std::vector<PagedTarget> targets;
    for (int row {0}; row < mGrid->rows(); ++row) {
        const glm::vec2 rect {mGrid->rowRect(row)};
        targets.push_back({0, row, mGrid->getPosition().y + rect.x,
                           mGrid->getPosition().y + rect.y});
    }
    bool moved {mScroll->pageScroll(direction)};
    const float viewTop {mScroll->scrollOffset()};
    const int pick {
        pickPagedTarget(targets, direction, viewTop, viewTop + mScroll->getSize().y)};
    if (pick >= 0) {
        const int columns {std::max(1, mGrid->columns())};
        const int column {mGrid->cursorIndex() % columns};
        const int target {
            std::min(targets[pick].aux * columns + column, mGrid->tileCount() - 1)};
        if (target != mGrid->cursorIndex()) {
            mGrid->setCursorIndex(target);
            moved = true;
        }
        followFocus();
    }
    if (moved)
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
}

std::vector<HelpPrompt> GuiMadPageBezelProject::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (mGrid != nullptr)
        prompts = mGrid->getHelpPrompts();
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void GuiMadPageBezelProject::onSaveFocus()
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void GuiMadPageBezelProject::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mGridCookie);
    if (mScroll != nullptr)
        mScroll->setScrollOffset(mScrollCookie);
    followFocus();
}

//  ── GuiMadPageBezelDetail (per-system actions) ──

GuiMadPageBezelDetail::GuiMadPageBezelDetail(GuiMadPanel* panel, const std::string& key,
                                             const std::string& label)
    : MadLightgunPageBase {panel, label}
    , mKey {key}
    , mLabel {label}
{
}

void GuiMadPageBezelDetail::build()
{
    if (!mBuilt) // refresh: keep current content until the new status swaps in
        setLoadingText("Loading…");
    const std::string key {mKey};
    pageRequest(
        "bezels.status",
        [key](MadJson::Writer& w) {
            w.Key("key");
            w.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't read bezel status: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            rebuild(payload);
        });
}

void GuiMadPageBezelDetail::action(const std::string& method, const std::string& doing,
                                   int timeoutMs)
{
    footer()->flash(doing, timeoutMs, false);
    const std::string key {mKey};
    pageRequest(
        method,
        [key](MadJson::Writer& w) {
            w.Key("key");
            w.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Failed: " + MadJson::getString(payload, "message", "error"),
                                5000, true);
                return;
            }
            footer()->flash("Done.", 2500, false);
            build(); // refresh status + the available actions
        },
        timeoutMs);
}

void GuiMadPageBezelDetail::rebuild(const rapidjson::Value& status)
{
    beginColumn();

    const bool repo {MadJson::getBool(status, "repo_present", false)};
    const bool installed {MadJson::getBool(status, "installed", false)};
    const int games {MadJson::getInt(status, "games", 0)};
    const int enabled {MadJson::getInt(status, "enabled", 0)};
    const float pad {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f};

    std::string head;
    if (!repo)
        head = "This bezel pack isn't downloaded under ~/Emulation/tools/bezelproject.";
    else if (!installed)
        head = "Not installed.";
    else if (games > 0)
        head = "Installed — " + std::to_string(games) + " games (" +
               std::to_string(enabled) + " enabled).";
    else
        head = "Installed, but no matching ROMs were found for this system.";
    addBlock(head, FONT_SIZE_SMALL, MadTheme::color(MadColor::Primary), pad);

    if (MadJson::getBool(status, "widescreen_warn", false))
        addBlock("This system has some 16:9 games; the 4:3 bezel can look wrong on those — "
                 "disable that system if it bothers you.",
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Red), pad * 0.5f);

    if (!repo) {
        addBlock("Download it with The Bezel Project, then reopen this page.",
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), pad);
        endColumn();
        return;
    }

    // install/uninstall touch many files (symlinks + per-game cfgs) — allow up to 3 min.
    // Actions sit side-by-side in one wrapping row (4-way nav).
    std::vector<std::pair<std::string, std::function<void()>>> row;
    if (!installed) {
        row.emplace_back("Install bezels",
                         [this] { action("bezels.install", "Installing bezels…", 180000); });
    }
    else {
        row.emplace_back("Re-install / update",
                         [this] { action("bezels.install", "Updating bezels…", 180000); });
        row.emplace_back("Remove bezels",
                         [this] { action("bezels.uninstall", "Removing bezels…", 180000); });
        if (games > 0) {
            row.emplace_back("Enable all",
                             [this] { action("bezels.enable", "Enabling…", 60000); });
            row.emplace_back("Disable all",
                             [this] { action("bezels.disable", "Disabling…", 60000); });
            const std::string key {mKey};
            const std::string label {mLabel};
            row.emplace_back("Per-game…", [this, key, label] {
                mPanel->pushPage(new GuiMadPageBezelPerGame(mPanel, key, label));
            });
        }
    }
    if (!row.empty())
        addButtonRow(row, true);
    endColumn();
}
