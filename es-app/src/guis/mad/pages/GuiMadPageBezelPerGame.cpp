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
#include "guis/mad/MadTheme.h"
#include "guis/mad/widgets/MadScrollView.h"

#include <algorithm>
#include <cctype>
#include <functional>
#include <utility>

namespace
{
    std::string lower(std::string s)
    {
        std::transform(s.begin(), s.end(), s.begin(),
                       [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        return s;
    }
    constexpr int kCap {300}; // cap unsearched huge lists (MAME ~2048) for responsiveness
} // namespace

GuiMadPageBezelPerGame::GuiMadPageBezelPerGame(GuiMadPanel* panel, const std::string& key,
                                               const std::string& label,
                                               const std::function<void()>& onChanged)
    : MadLightgunPageBase {panel, label}
    , mKey {key}
    , mLabel {label}
    , mOnChanged {onChanged}
{
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
                                      MadJson::getString(g, "preview")});
            populate();
        },
        8000);
}

void GuiMadPageBezelPerGame::populate()
{
    beginColumn();
    // Reserve the right ~40% of the viewport for the bezel preview pane.
    const float listWidth {mViewportSize.x * 0.60f};
    mScroll->setSize(listWidth, mViewportSize.y);

    const std::string f {lower(mFilter)};
    mShown.clear();
    for (const Game& g : mGames)
        if (f.empty() || lower(g.name).find(f) != std::string::npos)
            mShown.push_back(g);

    const bool capped {static_cast<int>(mShown.size()) > kCap};
    const float pad {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.3f};
    addBlock(std::to_string(mShown.size()) + (f.empty() ? " games" : " matches") +
                 " · press Y to search",
             FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), pad);

    // One toggle cell per row, stacked ("● name" on / "○ name" off). up/down walk
    // the list; A toggles. (A grid felt cramped for long game names, so it's stacked.)
    mGameButtons.clear();
    if (static_cast<int>(mShown.size()) > kCap)
        mShown.resize(kCap); // keep mShown parallel to the cells we actually render
    for (size_t i {0}; i < mShown.size(); ++i) {
        const Game& g {mShown[i]};
        mGameButtons.push_back(addButton((g.enabled ? "● " : "○ ") + g.name,
                                         [this, i] { toggleGame(static_cast<int>(i)); }));
    }
    if (capped)
        addBlock("…and more — press Y to search for a specific game.",
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), 0.0f);
    endColumn();

    if (mPreview == nullptr) {
        mPreview = std::make_shared<ImageComponent>();
        mPreview->setOrigin(0.5f, 0.0f);
        addChild(mPreview.get());
    }
    const float paneLeft {mViewportPos.x + listWidth};
    const float paneWidth {mViewportSize.x - listWidth};
    mPreview->setMaxSize(paneWidth * 0.9f, mViewportSize.y * 0.6f);
    mPreview->setPosition(paneLeft + paneWidth * 0.5f, mViewportPos.y);
    updatePreview();
}

void GuiMadPageBezelPerGame::updatePreview()
{
    if (mPreview == nullptr)
        return;
    if (mFocus >= 0 && mFocus < static_cast<int>(mShown.size()))
        mPreview->setImage(mShown[mFocus].preview); // empty path renders transparent (safe)
    else
        mPreview->setImage("");
}

void GuiMadPageBezelPerGame::toggleGame(int i)
{
    if (i < 0 || i >= static_cast<int>(mShown.size()))
        return;
    const bool on {!mShown[i].enabled};
    const std::string name {mShown[i].name};
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
        [this, i, name, on](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Failed: " + MadJson::getString(payload, "message", "error"), 4000,
                                true);
                return;
            }
            if (i < static_cast<int>(mShown.size()))
                mShown[i].enabled = on;
            if (i < static_cast<int>(mGameButtons.size())) {
                const std::string lbl {(on ? "● " : "○ ") + name};
                mGameButtons[i]->setText(lbl, lbl, false);
            }
            if (mOnChanged)
                mOnChanged(); // let the detail/grid know the enabled count changed
            footer()->flash((on ? "Enabled " : "Disabled ") + name, 2500, false);
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
    if (input.value != 0 && config->isMappedTo("y", input) && mBuilt) {
        openSearch();
        return true;
    }
    const bool handled {MadLightgunPageBase::input(config, input)};
    if (handled)
        updatePreview();
    return handled;
}

std::vector<HelpPrompt> GuiMadPageBezelPerGame::getHelpPrompts()
{
    return {HelpPrompt("up/down", "choose"), HelpPrompt("a", "toggle"),
            HelpPrompt("y", "search"), HelpPrompt("b", "back")};
}
