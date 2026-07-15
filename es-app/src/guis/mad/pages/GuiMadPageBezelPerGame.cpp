//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBezelPerGame.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageBezelPerGame.h"

#include "Window.h"
#include "guis/GuiTextEditKeyboardPopup.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadPageUtil.h"
#include "guis/mad/MadTheme.h"

#include <functional>

GuiMadPageBezelPerGame::GuiMadPageBezelPerGame(GuiMadPanel* panel, const std::string& key,
                                               const std::string& label,
                                               const std::function<void()>& onChanged)
    : MadPage {panel, label}
    , mKey {key}
    , mLabel {label}
    , mOnChanged {onChanged}
{
}

unsigned int GuiMadPageBezelPerGame::rowColor(const bool enabled)
{
    return enabled ? MadTheme::color(MadColor::Primary) : MadTheme::color(MadColor::Secondary);
}

void GuiMadPageBezelPerGame::build()
{
    setLoadingText("Loading games…");
    const std::string key {mKey};
    pageRequest(
        "bezels.games",
        [key](MadJson::Writer& w) {
            w.Key("key");
            w.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load games: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mGames.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "games")};
            if (arr.IsArray())
                for (const rapidjson::Value& g : arr.GetArray())
                    mGames.push_back({MadJson::getString(g, "game"),
                                      MadJson::getBool(g, "enabled"),
                                      MadJson::getString(g, "preview"),
                                      MadJson::getString(g, "title")});
            populate();
        },
        8000);
}

void GuiMadPageBezelPerGame::ensureWidgets()
{
    if (mList != nullptr)
        return;
    // Reserve the right ~40% of the viewport for the bezel preview pane; the list
    // sits in the left 60%, below a (up to two-line) header.
    const float listWidth {mViewportSize.x * 0.60f};
    const float headerHeight {Font::get(FONT_SIZE_SMALL)->getHeight() * 2.0f};

    mHeader = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                              MadTheme::color(MadColor::Secondary), ALIGN_LEFT,
                                              ALIGN_CENTER, glm::ivec2 {0, 1});
    mHeader->setPosition(mViewportPos.x, mViewportPos.y);
    mHeader->setSize(listWidth, 0.0f); // autosize height (may wrap to two lines)
    addChild(mHeader.get());

    const float listTop {mViewportPos.y + headerHeight};
    mList = std::make_shared<MadVirtualList>();
    mList->setPosition(mViewportPos.x, listTop);
    mList->setSize(listWidth, mViewportPos.y + mViewportSize.y - listTop);
    mList->setOnSelect([this](int i) { toggleGame(i); });
    mList->setOnCursorChanged([this](int) { updatePreview(); });
    addChild(mList.get());
    mList->onFocusGained(); // the only focusable widget on the page

    mPreview = MadPageUtil::makeBezelPreview(mViewportPos, mViewportSize, listWidth);
    addChild(mPreview.get());
}

void GuiMadPageBezelPerGame::populate()
{
    ensureWidgets();

    const std::string f {MadPageUtil::lower(mFilter)};
    mShown.clear();
    for (const Game& g : mGames)
        if (f.empty() || MadPageUtil::lower(rowText(g)).find(f) != std::string::npos ||
            MadPageUtil::lower(g.name).find(f) != std::string::npos) // match the title OR the rom stem
            mShown.push_back(g);

    mHeader->setText(std::to_string(mShown.size()) + (f.empty() ? " games" : " matches") +
                     " · press Y to search");

    // One row per shown game, stacked ("● name" on / "○ name" off). up/down walk
    // the list; A toggles. The list builds only the on-screen rows — no cap.
    std::vector<MadVirtualList::Row> rows;
    rows.reserve(mShown.size());
    for (const Game& g : mShown)
        rows.push_back({(g.enabled ? "● " : "○ ") + rowText(g), rowColor(g.enabled)});
    mList->setRows(rows, /*keepCursor=*/false); // a new filter lands at the top

    mPanel->refreshHelpPrompts();
    updatePreview();
}

void GuiMadPageBezelPerGame::updatePreview()
{
    if (mPreview == nullptr)
        return;
    const int c {mList != nullptr ? mList->cursor() : -1};
    if (c >= 0 && c < static_cast<int>(mShown.size()))
        mPreview->setImage(mShown[c].preview); // empty path renders transparent (safe)
    else
        mPreview->setImage("");
}

void GuiMadPageBezelPerGame::toggleGame(int i)
{
    if (i < 0 || i >= static_cast<int>(mShown.size()))
        return;
    const bool on {!mShown[i].enabled};
    const std::string name {mShown[i].name};      // stem — the bezels.disable_game write key
    const std::string disp {rowText(mShown[i])};  // human title for the relabel + flash
    const std::string key {mKey};
    pageRequest(
        "bezels.disable_game",
        [key, name, on](MadJson::Writer& w) {
            w.Key("key");
            w.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            w.Key("game");
            w.String(name.c_str(), static_cast<rapidjson::SizeType>(name.length()));
            w.Key("enabled");
            w.Bool(on);
        },
        [this, name, disp, on](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Failed: " + MadJson::getString(payload, "message", "error"), 4000,
                                true);
                return;
            }
            // Write through to the master list so a later populate()/search
            // rebuilds with the new state (the stem is the unique key).
            for (Game& gm : mGames)
                if (gm.name == name) {
                    gm.enabled = on;
                    break;
                }
            // Re-find the row by stem in the CURRENT filtered list (the user may
            // have searched while the write was in flight) and relabel it in place.
            for (size_t k {0}; k < mShown.size(); ++k) {
                if (mShown[k].name != name)
                    continue;
                mShown[k].enabled = on;
                if (mList != nullptr && static_cast<int>(k) < mList->size())
                    mList->setRow(static_cast<int>(k), (on ? "● " : "○ ") + disp, rowColor(on));
                break;
            }
            if (mOnChanged)
                mOnChanged(); // let the detail/grid know the enabled count changed
            footer()->flash((on ? "Enabled " : "Disabled ") + disp, 2500, false);
        },
        60000);
}

void GuiMadPageBezelPerGame::openSearch()
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiTextEditKeyboardPopup(
        0.0f, "Search " + mLabel, mFilter,
        [this, alive](const std::string& s) {
            if (alive.expired())
                return;
            mFilter = s;
            populate();
        },
        false, "SEARCH"));
}

bool GuiMadPageBezelPerGame::input(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("y", input) && mList != nullptr) {
        openSearch();
        return true;
    }
    return mList != nullptr ? mList->input(config, input) : false;
}

void GuiMadPageBezelPerGame::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

void GuiMadPageBezelPerGame::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->cursor();
}

void GuiMadPageBezelPerGame::onRestoreFocus()
{
    if (mList != nullptr)
        mList->setCursor(mFocusCookie);
}

std::vector<HelpPrompt> GuiMadPageBezelPerGame::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"), HelpPrompt("a", "toggle"),
                                     HelpPrompt("y", "search")};
    if (mList != nullptr && mList->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    prompts.push_back(HelpPrompt("b", "back"));
    return prompts;
}
