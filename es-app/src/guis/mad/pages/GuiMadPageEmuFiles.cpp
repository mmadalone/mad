//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEmuFiles.cpp  (deck-patches, P7)
//

#include "guis/mad/pages/GuiMadPageEmuFiles.h"

#include "components/TextComponent.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageEmu.h"
#include "guis/mad/MadPageUtil.h"
#include "resources/Font.h"

#include <cstdio>

GuiMadPageEmuFiles::GuiMadPageEmuFiles(GuiMadPanel* panel, GuiMadPageEmu* root, const std::string& source,
                                       const std::string& emulator, const std::string& label, bool restore)
    : MadPage {panel, label}
    , mRoot {root}
    , mSource {source}
    , mEmulator {emulator}
    , mLabel {label}
    , mRestore {restore}
{
}

std::string GuiMadPageEmuFiles::rowGlyph(const Group& g) const
{
    return g.selected ? "● " : "○ ";
}

unsigned int GuiMadPageEmuFiles::rowColor(const Group& g) const
{
    return g.selected ? MadTheme::color(MadColor::Primary) : MadTheme::color(MadColor::Secondary);
}

std::string GuiMadPageEmuFiles::rowText(const Group& g) const
{
    return g.label + "   " + MadPageUtil::humanSize(g.size);
}

std::string GuiMadPageEmuFiles::headerText() const
{
    int selected {0};
    for (const Group& g : mGroups)
        if (g.selected)
            ++selected;
    // The X (back up / restore) hint lives in the footer help row now; keep only the count here.
    return mLabel + "  ·  " + std::to_string(selected) + " of " + std::to_string(mGroups.size()) +
           " groups";
}

void GuiMadPageEmuFiles::build()
{
    setLoadingText("Reading emulator config…");
    const std::string source {mSource};
    const std::string emulator {mEmulator};
    pageRequest(
        "emucfg.files",
        [source, emulator](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
            w.Key("emulator");
            w.String(emulator.c_str(), static_cast<rapidjson::SizeType>(emulator.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't read emulator config: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mGroups.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "groups")};
            if (arr.IsArray())
                for (const rapidjson::Value& gv : arr.GetArray()) {
                    Group g;
                    g.key = MadJson::getString(gv, "key");
                    g.label = MadJson::getString(gv, "label");
                    g.size = MadJson::getInt64(gv, "size", 0);
                    g.count = MadJson::getInt(gv, "count", 0);
                    // pre-tick per the group's default (config/controller/keys/saves ON; textures/mods OFF).
                    g.selected = MadJson::getBool(gv, "default_ticked");
                    const rapidjson::Value& files {MadJson::getMember(gv, "files")};
                    if (files.IsArray())
                        for (const rapidjson::Value& f : files.GetArray()) {
                            const std::string rel {MadJson::getString(f, "rel")};
                            if (!rel.empty())
                                g.rels.push_back(rel);
                        }
                    if (!g.key.empty() && !g.rels.empty())
                        mGroups.push_back(g);
                }
            populate();
        },
        20000);
}

void GuiMadPageEmuFiles::ensureWidgets()
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

void GuiMadPageEmuFiles::populate()
{
    ensureWidgets();
    mHeader->setText(headerText());
    std::vector<MadVirtualList::Row> rows;
    rows.reserve(mGroups.size());
    for (const Group& g : mGroups)
        rows.push_back({rowGlyph(g) + rowText(g), rowColor(g)});
    if (rows.empty())
        rows.push_back({"Nothing to back up for this emulator.", MadTheme::color(MadColor::Secondary)});
    mList->setRows(rows, /*keepCursor=*/false);
    mPanel->refreshHelpPrompts();
}

void GuiMadPageEmuFiles::toggleAt(int i)
{
    if (i < 0 || i >= static_cast<int>(mGroups.size()))
        return;
    mGroups[i].selected = !mGroups[i].selected;
    if (i < mList->size())
        mList->setRow(i, rowGlyph(mGroups[i]) + rowText(mGroups[i]), rowColor(mGroups[i]));
    if (mHeader != nullptr)
        mHeader->setText(headerText());
}

void GuiMadPageEmuFiles::act()
{
    if (mRoot == nullptr)
        return;
    if (mRoot->busy()) {
        footer()->flash("A backup or restore is already running - let it finish first.", 4000, true);
        return;
    }
    std::vector<MadBackupItem> items;
    std::vector<std::string> rels;
    for (const Group& g : mGroups)
        if (g.selected)
            for (const std::string& rel : g.rels) {
                items.push_back({g.key, rel});
                rels.push_back(rel);
            }
    if (items.empty()) {
        footer()->flash(std::string("Pick at least one group to ") + (mRestore ? "restore" : "back up") +
                            " (press A to tick it).",
                        3500, false);
        return;
    }
    if (mRestore)
        mRoot->startRestore(mEmulator, rels);
    else
        mRoot->startBackup(mEmulator, items);
}

bool GuiMadPageEmuFiles::input(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("x", input)) {
        act();
        return true;
    }
    return mList != nullptr ? mList->input(config, input) : false;
}

void GuiMadPageEmuFiles::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

void GuiMadPageEmuFiles::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->cursor();
}

void GuiMadPageEmuFiles::onRestoreFocus()
{
    if (mList != nullptr)
        mList->setCursor(mFocusCookie);
}

std::vector<HelpPrompt> GuiMadPageEmuFiles::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"), HelpPrompt("a", "tick"),
                                     HelpPrompt("x", mRestore ? "restore" : "back up")};
    if (mList != nullptr && mList->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    prompts.push_back(HelpPrompt("b", "back"));
    return prompts;
}
