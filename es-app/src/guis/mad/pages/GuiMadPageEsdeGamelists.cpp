//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEsdeGamelists.cpp  (deck-patches, P6)
//

#include "guis/mad/pages/GuiMadPageEsdeGamelists.h"

#include "components/TextComponent.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageEsde.h"

GuiMadPageEsdeGamelists::GuiMadPageEsdeGamelists(GuiMadPanel* panel, GuiMadPageEsde* root,
                                                 const std::string& source)
    : MadPage {panel, "GAME FAVORITES & METADATA"}
    , mRoot {root}
    , mSel {root->gamelistSelection()}
    , mSource {source}
{
}

std::string GuiMadPageEsdeGamelists::rowText(const Sys& s) const
{
    return std::string(s.selected ? "● " : "○ ") + s.label;
}

std::string GuiMadPageEsdeGamelists::headerText() const
{
    int n {0};
    for (const Sys& s : mSystems)
        if (s.selected)
            ++n;
    return std::to_string(n) + " of " + std::to_string(mSystems.size()) +
           " systems · A tick · B done";
}

void GuiMadPageEsdeGamelists::build()
{
    setLoadingText("Loading systems…");
    const std::string source {mSource};
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "esde.gamelist_systems",
        [source](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load systems: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mSystems.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "systems")};
            if (arr.IsArray())
                for (const rapidjson::Value& s : arr.GetArray()) {
                    Sys sy;
                    sy.system = MadJson::getString(s, "system");
                    sy.label = MadJson::getString(s, "label");
                    sy.rel = MadJson::getString(s, "rel");
                    sy.size = MadJson::getInt64(s, "size", 0);
                    sy.selected = mSel != nullptr && mSel->count(sy.rel) > 0;
                    if (!sy.rel.empty())
                        mSystems.push_back(sy);
                }
            populate();
        },
        20000);
}

void GuiMadPageEsdeGamelists::ensureWidgets()
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

void GuiMadPageEsdeGamelists::populate()
{
    ensureWidgets();
    mHeader->setText(headerText());
    std::vector<MadVirtualList::Row> rows;
    rows.reserve(mSystems.size());
    for (const Sys& s : mSystems)
        rows.push_back({rowText(s), s.selected ? MadTheme::color(MadColor::Primary)
                                               : MadTheme::color(MadColor::Secondary)});
    mList->setRows(rows, /*keepCursor=*/true);
    mPanel->refreshHelpPrompts();
}

void GuiMadPageEsdeGamelists::toggleAt(int i)
{
    if (i < 0 || i >= static_cast<int>(mSystems.size()) || mSel == nullptr)
        return;
    Sys& s {mSystems[i]};
    s.selected = !s.selected;
    if (s.selected)
        mSel->insert(s.rel);
    else
        mSel->erase(s.rel);
    mHeader->setText(headerText());
    mList->setRow(i, rowText(s), s.selected ? MadTheme::color(MadColor::Primary)
                                            : MadTheme::color(MadColor::Secondary));
}

bool GuiMadPageEsdeGamelists::input(InputConfig* config, Input input)
{
    return mList != nullptr && mList->input(config, input);
}

void GuiMadPageEsdeGamelists::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

void GuiMadPageEsdeGamelists::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->cursor();
}

void GuiMadPageEsdeGamelists::onRestoreFocus()
{
    if (mList != nullptr)
        mList->setCursor(mFocusCookie);
}

std::vector<HelpPrompt> GuiMadPageEsdeGamelists::getHelpPrompts()
{
    return mList != nullptr ? mList->getHelpPrompts() : std::vector<HelpPrompt>();
}
