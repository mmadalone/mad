//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageAssetList.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageAssetList.h"

#include "Window.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadPageUtil.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackupRestore.h"
#include "resources/Font.h"

#include <cstdio>

namespace
{
    std::string humanSize(long long bytes)
    {
        if (bytes < 1024)
            return std::to_string(bytes) + " B";
        const char* unit[] {"KB", "MB", "GB", "TB"};
        double v {static_cast<double>(bytes)};
        int i {-1};
        while (v >= 1024.0 && i < 3) {
            v /= 1024.0;
            ++i;
        }
        char buf[32];
        std::snprintf(buf, sizeof buf, "%.1f %s", v, unit[i]);
        return buf;
    }
}

GuiMadPageAssetList::GuiMadPageAssetList(GuiMadPanel* panel, GuiMadPageBackupRestore* root,
                                        const std::string& source, const std::string& system,
                                        const std::string& stem, const std::string& name,
                                        const std::string& art)
    : MadPage {panel, name.empty() ? stem : name}
    , mRoot {root}
    , mSource {source}
    , mSystem {system}
    , mStem {stem}
    , mName {name.empty() ? stem : name}
    , mArt {art}
{
}

std::string GuiMadPageAssetList::rowGlyph(const Asset& a) const
{
    if (!a.present)
        return "- ";
    return a.selected ? "● " : "○ ";
}

unsigned int GuiMadPageAssetList::rowColor(const Asset& a) const
{
    if (!a.present)
        return MadTheme::color(MadColor::Secondary);
    return a.selected ? MadTheme::color(MadColor::Primary) : MadTheme::color(MadColor::Secondary);
}

std::string GuiMadPageAssetList::rowText(const Asset& a) const
{
    std::string t {a.label + "   "};
    if (!a.present) {
        t += "none";
        return t;
    }
    t += humanSize(a.size);
    if (a.count > 1)
        t += "  (" + std::to_string(a.count) + " files)";
    return t;
}

std::string GuiMadPageAssetList::headerText() const
{
    int present {0}, selected {0};
    for (const Asset& a : mAssets) {
        if (a.present)
            ++present;
        if (a.present && a.selected)
            ++selected;
    }
    return mName + "  ·  " + std::to_string(selected) + " of " + std::to_string(present) +
           " selected  ·  X back up";
}

void GuiMadPageAssetList::build()
{
    setLoadingText("Reading this game…");
    const std::string source {mSource};
    const std::string system {mSystem};
    const std::string stem {mStem};
    pageRequest(
        "granular.game_assets",
        [source, system, stem](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
            w.Key("system");
            w.String(system.c_str(), static_cast<rapidjson::SizeType>(system.length()));
            w.Key("game");
            w.String(stem.c_str(), static_cast<rapidjson::SizeType>(stem.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't read this game: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mAssets.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "assets")};
            if (arr.IsArray())
                for (const rapidjson::Value& a : arr.GetArray()) {
                    Asset asset;
                    asset.key = MadJson::getString(a, "key");
                    asset.label = MadJson::getString(a, "label");
                    asset.category = MadJson::getString(a, "category");
                    asset.present = MadJson::getBool(a, "present");
                    asset.size = MadJson::getInt64(a, "size", 0); // 64-bit: a multi-GB game folder
                    asset.count = MadJson::getInt(a, "count", 0);
                    asset.selected = asset.present; // pre-tick everything present ("back up all" = one X)
                    mAssets.push_back(asset);
                }
            populate();
        },
        12000);
}

void GuiMadPageAssetList::ensureWidgets()
{
    if (mList != nullptr)
        return;
    const float listWidth {mViewportSize.x * 0.60f};
    const float headerHeight {Font::get(FONT_SIZE_SMALL)->getHeight() * 2.0f};

    mHeader = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                              MadTheme::color(MadColor::Secondary), ALIGN_LEFT,
                                              ALIGN_CENTER, glm::ivec2 {0, 1});
    mHeader->setPosition(mViewportPos.x, mViewportPos.y);
    mHeader->setSize(listWidth, 0.0f);
    addChild(mHeader.get());

    const float listTop {mViewportPos.y + headerHeight};
    mList = std::make_shared<MadVirtualList>();
    mList->setPosition(mViewportPos.x, listTop);
    mList->setSize(listWidth, mViewportPos.y + mViewportSize.y - listTop);
    mList->setOnSelect([this](int i) { toggleAt(i); });
    addChild(mList.get());
    mList->onFocusGained();

    mPreview = MadPageUtil::makeBezelPreview(mViewportPos, mViewportSize, listWidth);
    mPreview->setImage(mArt); // the game's box art, static for the page
    addChild(mPreview.get());
}

void GuiMadPageAssetList::populate()
{
    ensureWidgets();
    mHeader->setText(headerText());
    std::vector<MadVirtualList::Row> rows;
    rows.reserve(mAssets.size());
    for (const Asset& a : mAssets)
        rows.push_back({rowGlyph(a) + rowText(a), rowColor(a)});
    if (rows.empty())
        rows.push_back({"Nothing to back up for this game.", MadTheme::color(MadColor::Secondary)});
    mList->setRows(rows, /*keepCursor=*/false);
    mPanel->refreshHelpPrompts();
}

void GuiMadPageAssetList::toggleAt(int i)
{
    if (i < 0 || i >= static_cast<int>(mAssets.size()))
        return;
    if (!mAssets[i].present) {
        footer()->flash("This game has no " + mAssets[i].label + ".", 2500, false);
        return;
    }
    mAssets[i].selected = !mAssets[i].selected;
    if (i < mList->size())
        mList->setRow(i, rowGlyph(mAssets[i]) + rowText(mAssets[i]), rowColor(mAssets[i]));
    if (mHeader != nullptr)
        mHeader->setText(headerText());
}

void GuiMadPageAssetList::act()
{
    if (mRoot == nullptr)
        return;
    if (mRoot->busy()) {
        // startGameAssets sets the root's mRunning synchronously, so busy() also guards a double X-press.
        footer()->flash("A backup or restore is already running — let it finish first.", 4000, true);
        return;
    }
    std::vector<std::string> keys;
    for (const Asset& a : mAssets)
        if (a.present && a.selected)
            keys.push_back(a.key);
    if (keys.empty()) {
        footer()->flash("Pick at least one thing to back up (press A to tick it).", 3500, false);
        return;
    }
    mRoot->startGameAssets(mSystem, mStem, keys);
    footer()->setStatus("Backing up " + mName + "…");
}

bool GuiMadPageAssetList::input(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("x", input)) {
        act();
        return true;
    }
    return mList != nullptr ? mList->input(config, input) : false;
}

bool GuiMadPageAssetList::consumesSectionNav()
{
    return mRoot != nullptr && mRoot->busy();
}

void GuiMadPageAssetList::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

void GuiMadPageAssetList::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->cursor();
}

void GuiMadPageAssetList::onRestoreFocus()
{
    if (mList != nullptr)
        mList->setCursor(mFocusCookie);
}

std::vector<HelpPrompt> GuiMadPageAssetList::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"), HelpPrompt("a", "tick"),
                                     HelpPrompt("x", "back up")};
    if (mList != nullptr && mList->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    prompts.push_back(HelpPrompt("b", "back"));
    return prompts;
}
