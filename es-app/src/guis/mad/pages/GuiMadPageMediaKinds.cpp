//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageMediaKinds.cpp  (deck-patches, P4)
//

#include "guis/mad/pages/GuiMadPageMediaKinds.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageAssetList.h"
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

GuiMadPageMediaKinds::GuiMadPageMediaKinds(GuiMadPanel* panel, GuiMadPageAssetList* owner,
                                           const std::string& source, const std::string& system,
                                           const std::string& stem)
    : MadPage {panel, "MEDIA KINDS"}
    , mOwner {owner}
    , mSource {source}
    , mSystem {system}
    , mStem {stem}
{
}

std::string GuiMadPageMediaKinds::rowText(const Kind& k) const
{
    return k.label + "   " + humanSize(k.size);
}

std::string GuiMadPageMediaKinds::headerText() const
{
    int sel {0};
    if (mSel != nullptr)
        for (const Kind& k : mKinds)
            if (mSel->count(k.key))
                ++sel;
    return "Media to include  ·  " + std::to_string(sel) + " of " +
           std::to_string(static_cast<int>(mKinds.size())) + " kinds  ·  A tick";
}

void GuiMadPageMediaKinds::build()
{
    setLoadingText("Reading media…");
    const std::string source {mSource};
    const std::string system {mSystem};
    const std::string stem {mStem};
    pageRequest(
        "granular.game_media",
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
                footer()->setStatus("Couldn't read media: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mKinds.clear();
            std::vector<std::string> keys;
            const rapidjson::Value& arr {MadJson::getMember(payload, "kinds")};
            if (arr.IsArray())
                for (const rapidjson::Value& k : arr.GetArray()) {
                    Kind kd;
                    kd.key = MadJson::getString(k, "key");
                    kd.label = MadJson::getString(k, "label");
                    kd.size = MadJson::getInt64(k, "size", 0);
                    if (kd.key.empty())
                        continue;
                    mKinds.push_back(kd);
                    keys.push_back(kd.key);
                }
            // seed / cache in the owner (first drill inherits the coarse Media tick), then tick into its set.
            if (mOwner != nullptr) {
                mOwner->beginMediaDrill(keys);
                mSel = &mOwner->mediaKindSel();
            }
            populate();
        },
        12000);
}

void GuiMadPageMediaKinds::ensureWidgets()
{
    if (mList != nullptr)
        return;
    const float headerHeight {Font::get(FONT_SIZE_SMALL)->getHeight() * 2.0f};
    mHeader = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                              MadTheme::color(MadColor::Secondary), ALIGN_LEFT,
                                              ALIGN_CENTER, glm::ivec2 {0, 1});
    mHeader->setPosition(mViewportPos.x, mViewportPos.y);
    mHeader->setSize(mViewportSize.x, 0.0f);
    addChild(mHeader.get());

    const float listTop {mViewportPos.y + headerHeight};
    mList = std::make_shared<MadVirtualList>();
    mList->setPosition(mViewportPos.x, listTop);
    mList->setSize(mViewportSize.x, mViewportPos.y + mViewportSize.y - listTop);
    mList->setOnSelect([this](int i) { toggleAt(i); });
    addChild(mList.get());
    mList->onFocusGained();
}

void GuiMadPageMediaKinds::populate()
{
    ensureWidgets();
    mHeader->setText(headerText());
    std::vector<MadVirtualList::Row> rows;
    rows.reserve(mKinds.size());
    for (const Kind& k : mKinds) {
        const bool on {mSel != nullptr && mSel->count(k.key) > 0};
        rows.push_back({(on ? "● " : "○ ") + rowText(k),
                        on ? MadTheme::color(MadColor::Primary) : MadTheme::color(MadColor::Secondary)});
    }
    if (rows.empty())
        rows.push_back({"This game has no downloaded media.", MadTheme::color(MadColor::Secondary)});
    mList->setRows(rows, /*keepCursor=*/true);
    mPanel->refreshHelpPrompts();
}

void GuiMadPageMediaKinds::toggleAt(int i)
{
    if (mSel == nullptr || i < 0 || i >= static_cast<int>(mKinds.size()))
        return;
    const std::string& key {mKinds[i].key};
    if (mSel->count(key))
        mSel->erase(key);
    else
        mSel->insert(key);
    const bool on {mSel->count(key) > 0};
    if (i < mList->size())
        mList->setRow(i, (on ? "● " : "○ ") + rowText(mKinds[i]),
                      on ? MadTheme::color(MadColor::Primary) : MadTheme::color(MadColor::Secondary));
    if (mHeader != nullptr)
        mHeader->setText(headerText());
}

bool GuiMadPageMediaKinds::input(InputConfig* config, Input input)
{
    return mList != nullptr ? mList->input(config, input) : false;
}

void GuiMadPageMediaKinds::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

void GuiMadPageMediaKinds::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->cursor();
}

void GuiMadPageMediaKinds::onRestoreFocus()
{
    if (mList != nullptr)
        mList->setCursor(mFocusCookie);
}

std::vector<HelpPrompt> GuiMadPageMediaKinds::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"), HelpPrompt("a", "tick")};
    if (mList != nullptr && mList->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    prompts.push_back(HelpPrompt("b", "done"));
    return prompts;
}
