//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageManageSets.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageManageSets.h"

#include "Window.h"
#include "components/TextComponent.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadTheme.h"
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

    std::string fmtDate(const std::string& ts)
    {
        if (ts.size() == 15 && ts[8] == 'T')
            return ts.substr(0, 4) + "-" + ts.substr(4, 2) + "-" + ts.substr(6, 2) + " " +
                   ts.substr(9, 2) + ":" + ts.substr(11, 2);
        return ts.empty() ? "unknown date" : ts;
    }

    std::string catLabel(const std::string& c)
    {
        if (c == "games")
            return "Games";
        if (c == "emucfg")
            return "Emulator config";
        if (c == "esde")
            return "ES-DE settings";
        if (c == "bios")
            return "BIOS";
        if (c == "system")
            return "System";
        if (c == "controllers")
            return "Controller config";
        return c;
    }

    std::string countNoun(const std::string& cat, int n)
    {
        const std::string base {cat == "games" ? "game" : "item"};
        return std::to_string(n) + " " + base + (n == 1 ? "" : "s");
    }
}

GuiMadPageManageSets::GuiMadPageManageSets(GuiMadPanel* panel, const std::string& category,
                                          const std::string& label)
    : MadPage {panel, label}
    , mCategory {category}
    , mLabel {label}
{
}

void GuiMadPageManageSets::build()
{
    fetchSets();
}

void GuiMadPageManageSets::fetchSets()
{
    setLoadingText("Loading backups…");
    const std::string cat {mCategory};
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "manage.list", [](MadJson::Writer&) {},
        [this, alive, cat](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load backups: " +
                                        MadJson::getString(payload, "message", "error"),
                                    true);
                return;
            }
            mSets.clear();
            for (const char* key : {"local", "cloud"}) {
                const rapidjson::Value& arr {MadJson::getMember(payload, key)};
                if (!arr.IsArray())
                    continue;
                for (const rapidjson::Value& r : arr.GetArray()) {
                    Set s;
                    s.scope = MadJson::getString(r, "scope");
                    s.category = MadJson::getString(r, "category");
                    s.id = MadJson::getString(r, "id");
                    s.token = MadJson::getString(r, "token");
                    s.created = MadJson::getString(r, "created");
                    s.size = MadJson::getInt64(r, "size", 0);
                    s.count = MadJson::getInt(r, "count", 0);
                    if (s.id.empty())
                        continue;
                    if (cat != "all" && s.category != cat)
                        continue;
                    mSets.push_back(s);
                }
            }
            populate();
        },
        20000);
}

void GuiMadPageManageSets::populate()
{
    if (mList == nullptr) {
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
        mList->setOnSelect([this](int i) { activateAt(i); });
        addChild(mList.get());
        mList->onFocusGained();
    }
    mHeader->setText(headerText());
    std::vector<MadVirtualList::Row> rows;
    for (const Set& s : mSets)
        rows.push_back({rowText(s), MadTheme::color(MadColor::Primary)});
    if (hasDeleteAllRow()) {
        // the bulk "delete all" row lives LAST, not first, so the cursor never DEFAULTS onto a destructive
        // action (a reflexive double-A on page open can't bulk-delete; the cursor lands on the first set).
        const std::string lbl {
            mCategory == "all"
                ? ("> Delete ALL " + std::to_string(mSets.size()) + " backups (everything)")
                : ("> Delete all " + std::to_string(mSets.size()) + " " + mLabel + " backups")};
        rows.push_back({lbl, MadTheme::color(MadColor::Red)});
    }
    if (rows.empty())
        rows.push_back({"No backups here yet.", MadTheme::color(MadColor::Secondary)});
    mList->setRows(rows, /*keepCursor=*/false);
    mPanel->refreshHelpPrompts();
}

std::string GuiMadPageManageSets::headerText() const
{
    return mLabel + "  ·  " + std::to_string(mSets.size()) +
           (mSets.size() == 1 ? " backup" : " backups");
}

std::string GuiMadPageManageSets::rowText(const Set& s) const
{
    std::string out;
    if (mCategory == "all")
        out += catLabel(s.category) + "  ·  ";
    out += fmtDate(s.created);
    out += "  ·  " + countNoun(s.category, s.count);
    out += "  ·  ";
    out += (s.scope == "cloud") ? "MEGA" : "On this Deck";
    if (s.size > 0)
        out += "  ·  " + humanSize(s.size);
    return out;
}

std::string GuiMadPageManageSets::setDesc(const Set& s) const
{
    return catLabel(s.category) + " - " + fmtDate(s.created) + " - " + countNoun(s.category, s.count) +
           " - " + ((s.scope == "cloud") ? "MEGA cloud" : "On this Deck");
}

void GuiMadPageManageSets::activateAt(int i)
{
    if (mBusy || mSets.empty())
        return;
    if (hasDeleteAllRow() && i == static_cast<int>(mSets.size())) { // the LAST row = bulk delete
        confirmDeleteAll();
        return;
    }
    if (i >= 0 && i < static_cast<int>(mSets.size()))
        confirmDeleteOne(mSets[i]);
}

void GuiMadPageManageSets::confirmDeleteOne(const Set& s)
{
    if (mBusy)
        return;
    const Set copy {s}; // capture by value: the confirm is async, the list may be refetched under it
    const std::string body {"Permanently delete this backup?\n\n" + setDesc(copy) +
                            "\n\nThis CANNOT be undone. Your games, saves and settings are NOT affected."};
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new MadMsgBox(body, "DELETE",
                                  [this, alive, copy] {
                                      if (!alive.expired())
                                          doDeleteOne(copy);
                                  },
                                  "CANCEL", nullptr));
}

void GuiMadPageManageSets::confirmDeleteAll()
{
    if (mBusy || mSets.empty())
        return;
    const int n {static_cast<int>(mSets.size())};
    const std::string body {
        mCategory == "all"
            ? ("Permanently delete ALL " + std::to_string(n) +
               " backup(s) across EVERY category (On this Deck and MEGA)?\n\nThis CANNOT be undone. Your "
               "games, saves and settings are NOT affected.")
            : ("Permanently delete all " + std::to_string(n) + " " + mLabel +
               " backup(s)?\n\nThis CANNOT be undone. Your games, saves and settings are NOT affected.")};
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new MadMsgBox(body, "DELETE ALL",
                                  [this, alive] {
                                      if (!alive.expired())
                                          doDeleteAll();
                                  },
                                  "CANCEL", nullptr));
}

void GuiMadPageManageSets::doDeleteOne(const Set& s)
{
    if (mBusy)
        return;
    mBusy = true;
    footer()->setStatus("Deleting…");
    const Set copy {s};
    // ONE predicate for both routing and display (rowText/setDesc also branch on scope=="cloud"), so a
    // blank/unexpected scope can never show as local yet delete via the cloud path with an empty token.
    const bool isCloud {copy.scope == "cloud"};
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        isCloud ? "cloud.delete_set" : "manage.delete_local",
        [copy, isCloud](MadJson::Writer& w) {
            if (!isCloud) {
                w.Key("path");
                w.String(copy.id.c_str(), static_cast<rapidjson::SizeType>(copy.id.length()));
            }
            else {
                w.Key("category");
                w.String(copy.category.c_str(), static_cast<rapidjson::SizeType>(copy.category.length()));
                w.Key("token");
                w.String(copy.token.c_str(), static_cast<rapidjson::SizeType>(copy.token.length()));
            }
        },
        [this, alive](bool ok, const rapidjson::Value& p) {
            if (alive.expired())
                return;
            mBusy = false;
            footer()->setStatus("");
            if (!ok) {
                footer()->flash("Couldn't delete: " + MadJson::getString(p, "message", "error"), 5000,
                                true);
                return;
            }
            footer()->flash("Backup deleted.", 3000, false);
            fetchSets();
        },
        60000);
}

void GuiMadPageManageSets::doDeleteAll()
{
    if (mBusy)
        return;
    mBusy = true;
    footer()->setStatus("Deleting…");
    const std::string cat {mCategory};
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "manage.delete_all",
        [cat](MadJson::Writer& w) {
            if (cat != "all") {
                w.Key("category");
                w.String(cat.c_str(), static_cast<rapidjson::SizeType>(cat.length()));
            }
        },
        [this, alive](bool ok, const rapidjson::Value& p) {
            if (alive.expired())
                return;
            mBusy = false;
            footer()->setStatus("");
            if (!ok) {
                footer()->flash("Couldn't delete: " + MadJson::getString(p, "message", "error"), 5000,
                                true);
                return;
            }
            const int del {MadJson::getInt(p, "deleted", 0)};
            const int fail {MadJson::getInt(p, "failed", 0)};
            const bool connected {MadJson::getBool(p, "connected", true)};
            std::string msg {"Deleted " + std::to_string(del) + " backup(s)"};
            if (fail > 0)
                msg += " (" + std::to_string(fail) + " could not be deleted)";
            if (!connected)
                msg += "; some MEGA backups may remain (couldn't reach the cloud)";
            footer()->flash(msg + ".", 4500, fail > 0 || !connected);
            fetchSets();
        },
        120000);
}

bool GuiMadPageManageSets::input(InputConfig* config, Input input)
{
    return mList != nullptr ? mList->input(config, input) : false;
}

void GuiMadPageManageSets::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

void GuiMadPageManageSets::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->cursor();
}

void GuiMadPageManageSets::onRestoreFocus()
{
    if (mList != nullptr)
        mList->setCursor(mFocusCookie);
}

std::vector<HelpPrompt> GuiMadPageManageSets::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"), HelpPrompt("a", "delete")};
    if (mList != nullptr && mList->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    prompts.push_back(HelpPrompt("b", "back"));
    return prompts;
}
