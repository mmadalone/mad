//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBiosFiles.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageBiosFiles.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBios.h"
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

GuiMadPageBiosFiles::GuiMadPageBiosFiles(GuiMadPanel* panel, GuiMadPageBios* root,
                                        const std::string& source, const std::string& bucket,
                                        const std::string& label, bool restore)
    : MadPage {panel, label}
    , mRoot {root}
    , mSource {source}
    , mBucket {bucket}
    , mLabel {label}
    , mRestore {restore}
{
}

std::string GuiMadPageBiosFiles::rowGlyph(const File& f) const
{
    return f.selected ? "● " : "○ ";
}

unsigned int GuiMadPageBiosFiles::rowColor(const File& f) const
{
    return f.selected ? MadTheme::color(MadColor::Primary) : MadTheme::color(MadColor::Secondary);
}

std::string GuiMadPageBiosFiles::rowText(const File& f) const
{
    return f.name + "   " + humanSize(f.size);
}

std::string GuiMadPageBiosFiles::headerText() const
{
    int selected {0};
    for (const File& f : mFiles)
        if (f.selected)
            ++selected;
    // The X (back up / restore) hint lives in the footer help row now; keep only the count here.
    return mLabel + "  ·  " + std::to_string(selected) + " of " + std::to_string(mFiles.size()) +
           " selected";
}

void GuiMadPageBiosFiles::build()
{
    setLoadingText("Reading BIOS files…");
    const std::string source {mSource};
    const std::string bucket {mBucket};
    pageRequest(
        "bios.files",
        [source, bucket](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
            w.Key("bucket");
            w.String(bucket.c_str(), static_cast<rapidjson::SizeType>(bucket.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't read BIOS files: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mFiles.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "files")};
            if (arr.IsArray())
                for (const rapidjson::Value& f : arr.GetArray()) {
                    File file;
                    file.rel = MadJson::getString(f, "rel");
                    file.name = MadJson::getString(f, "name");
                    file.size = MadJson::getInt64(f, "size", 0);
                    file.selected = true; // pre-tick all ("back up / restore everything" = one X)
                    if (!file.rel.empty())
                        mFiles.push_back(file);
                }
            populate();
        },
        12000);
}

void GuiMadPageBiosFiles::ensureWidgets()
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

void GuiMadPageBiosFiles::populate()
{
    ensureWidgets();
    mHeader->setText(headerText());
    std::vector<MadVirtualList::Row> rows;
    rows.reserve(mFiles.size());
    for (const File& f : mFiles)
        rows.push_back({rowGlyph(f) + rowText(f), rowColor(f)});
    if (rows.empty())
        rows.push_back({"No BIOS files in this group.", MadTheme::color(MadColor::Secondary)});
    mList->setRows(rows, /*keepCursor=*/false);
    mPanel->refreshHelpPrompts();
}

void GuiMadPageBiosFiles::toggleAt(int i)
{
    if (i < 0 || i >= static_cast<int>(mFiles.size()))
        return;
    mFiles[i].selected = !mFiles[i].selected;
    if (i < mList->size())
        mList->setRow(i, rowGlyph(mFiles[i]) + rowText(mFiles[i]), rowColor(mFiles[i]));
    if (mHeader != nullptr)
        mHeader->setText(headerText());
}

void GuiMadPageBiosFiles::act()
{
    if (mRoot == nullptr)
        return;
    if (mRoot->busy()) {
        footer()->flash("A backup or restore is already running — let it finish first.", 4000, true);
        return;
    }
    std::vector<std::string> rels;
    for (const File& f : mFiles)
        if (f.selected)
            rels.push_back(f.rel);
    if (rels.empty()) {
        footer()->flash(std::string("Pick at least one file to ") + (mRestore ? "restore" : "back up") +
                            " (press A to tick it).",
                        3500, false);
        return;
    }
    if (mRestore)
        mRoot->startBiosRestore(mBucket, rels);
    else
        mRoot->startBiosBackup(mBucket, rels);
}

bool GuiMadPageBiosFiles::input(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("x", input)) {
        act();
        return true;
    }
    return mList != nullptr ? mList->input(config, input) : false;
}

bool GuiMadPageBiosFiles::consumesSectionNav()
{
    // Leaving during a job is allowed now (the durable root's op keeps running on the daemon), so the
    // shoulder section-switch no longer needs to be blocked here either.
    return false;
}

void GuiMadPageBiosFiles::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

void GuiMadPageBiosFiles::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->cursor();
}

void GuiMadPageBiosFiles::onRestoreFocus()
{
    if (mList != nullptr)
        mList->setCursor(mFocusCookie);
}

std::vector<HelpPrompt> GuiMadPageBiosFiles::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"), HelpPrompt("a", "tick"),
                                     HelpPrompt("x", mRestore ? "restore" : "back up")};
    if (mList != nullptr && mList->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    prompts.push_back(HelpPrompt("b", "back"));
    return prompts;
}
