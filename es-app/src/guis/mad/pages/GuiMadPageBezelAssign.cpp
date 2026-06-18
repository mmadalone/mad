//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBezelAssign.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageBezelAssign.h"

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
    constexpr int kCap {300}; // cap unsearched huge lists (Dreamcast ~1178 bezels) for responsiveness
} // namespace

//  ── GuiMadPageBezelAssign (target-game picker) ──

GuiMadPageBezelAssign::GuiMadPageBezelAssign(GuiMadPanel* panel, const std::string& key,
                                             const std::string& label,
                                             const std::function<void()>& onChanged)
    : MadLightgunPageBase {panel, label + " — ASSIGN BEZEL"}
    , mKey {key}
    , mLabel {label}
    , mOnChanged {onChanged}
{
}

void GuiMadPageBezelAssign::build()
{
    setLoadingText("Loading games…");
    const std::string key {mKey};
    pageRequest(
        "bezels.roms",
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
            mRoms.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "roms")};
            if (arr.IsArray())
                for (const rapidjson::Value& r : arr.GetArray())
                    mRoms.push_back({MadJson::getString(r, "game"),
                                     MadJson::getString(r, "assigned"),
                                     MadJson::getBool(r, "has_own_bezel")});
            populate();
        },
        20000);
}

void GuiMadPageBezelAssign::onChildPopped()
{
    if (mDirty) {
        mDirty = false;
        build(); // an assignment happened — re-fetch so the new "→ source" shows
    }
}

void GuiMadPageBezelAssign::populate()
{
    beginColumn();
    const std::string f {lower(mFilter)};
    mShown.clear();
    for (const Rom& r : mRoms)
        if (f.empty() || lower(r.game).find(f) != std::string::npos)
            mShown.push_back(r);

    const bool capped {static_cast<int>(mShown.size()) > kCap};
    const float pad {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.3f};
    addBlock("Pick a game, then choose an existing bezel for it.  " +
                 std::to_string(mShown.size()) + (f.empty() ? " games" : " matches") +
                 " · press Y to search",
             FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), pad);

    mButtons.clear();
    if (capped)
        mShown.resize(kCap); // keep mShown parallel to the cells we actually render
    for (size_t i {0}; i < mShown.size(); ++i) {
        const Rom& r {mShown[i]};
        const std::string hint {r.assigned.empty() ? "  ·  (no bezel)" : "  →  " + r.assigned};
        mButtons.push_back(addButton(r.game + hint,
                                     [this, i] { pickSource(static_cast<int>(i)); }));
    }
    if (capped)
        addBlock("…and more — press Y to search for a specific game.",
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), 0.0f);
    endColumn();
}

void GuiMadPageBezelAssign::pickSource(int i)
{
    if (i < 0 || i >= static_cast<int>(mShown.size()))
        return;
    const std::string target {mShown[i].game};
    std::weak_ptr<int> alive {pageAlive()};
    mPanel->pushPage(new GuiMadPageBezelSource(mPanel, mKey, target, [this, alive] {
        if (alive.expired())      // this target page was popped during the assign request
            return;
        mDirty = true;            // refresh this target list's "→ source" on return
        if (mOnChanged)
            mOnChanged();         // the Bezel detail page's game/enabled count changed too
    }));
}

void GuiMadPageBezelAssign::openSearch()
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

bool GuiMadPageBezelAssign::input(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("y", input) && mBuilt) {
        openSearch();
        return true;
    }
    return MadLightgunPageBase::input(config, input);
}

std::vector<HelpPrompt> GuiMadPageBezelAssign::getHelpPrompts()
{
    return {HelpPrompt("up/down", "choose"), HelpPrompt("a", "pick bezel"),
            HelpPrompt("y", "search"), HelpPrompt("b", "back")};
}

//  ── GuiMadPageBezelSource (source-bezel picker, with preview) ──

GuiMadPageBezelSource::GuiMadPageBezelSource(GuiMadPanel* panel, const std::string& key,
                                             const std::string& target,
                                             const std::function<void()>& onAssigned)
    : MadLightgunPageBase {panel, "PICK A BEZEL"}
    , mKey {key}
    , mTarget {target}
    , mOnAssigned {onAssigned}
{
}

void GuiMadPageBezelSource::build()
{
    setLoadingText("Loading bezels…");
    const std::string key {mKey};
    pageRequest(
        "bezels.available",
        [key](MadJson::Writer& w) {
            w.Key("key");
            w.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load bezels: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mBezels.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "bezels")};
            if (arr.IsArray())
                for (const rapidjson::Value& b : arr.GetArray())
                    mBezels.push_back(
                        {MadJson::getString(b, "name"), MadJson::getString(b, "preview")});
            populate();
        },
        15000);
}

void GuiMadPageBezelSource::populate()
{
    beginColumn();
    // Reserve the right ~40% of the viewport for the bezel preview pane (mirrors
    // GuiMadPageBezelPerGame's split).
    const float listWidth {mViewportSize.x * 0.60f};
    mScroll->setSize(listWidth, mViewportSize.y);

    const std::string f {lower(mFilter)};
    mShown.clear();
    for (const Bezel& b : mBezels)
        if (f.empty() || lower(b.name).find(f) != std::string::npos)
            mShown.push_back(b);

    const bool capped {static_cast<int>(mShown.size()) > kCap};
    const float pad {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.3f};
    addBlock("Bezel for \"" + mTarget + "\" · " + std::to_string(mShown.size()) +
                 (f.empty() ? " bezels" : " matches") + " · press Y to search",
             FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), pad);

    mButtons.clear();
    if (capped)
        mShown.resize(kCap);
    for (size_t i {0}; i < mShown.size(); ++i) {
        const Bezel& b {mShown[i]};
        mButtons.push_back(addButton(b.name, [this, i] { assign(static_cast<int>(i)); }));
    }
    if (capped)
        addBlock("…and more — press Y to search for a specific bezel.",
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

void GuiMadPageBezelSource::updatePreview()
{
    if (mPreview == nullptr)
        return;
    if (mFocus >= 0 && mFocus < static_cast<int>(mShown.size()))
        mPreview->setImage(mShown[mFocus].preview); // empty path renders transparent (safe)
    else
        mPreview->setImage("");
}

void GuiMadPageBezelSource::assign(int i)
{
    if (i < 0 || i >= static_cast<int>(mShown.size()))
        return;
    if (mAssignInFlight)          // ignore a second A-press while a write is still pending
        return;
    mAssignInFlight = true;
    const std::string source {mShown[i].name};
    const std::string target {mTarget};
    const std::string key {mKey};
    footer()->flash("Assigning " + source + "…", 8000, false);
    pageRequest(
        "bezels.assign",
        [key, target, source](MadJson::Writer& w) {
            w.Key("key");
            w.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            w.Key("target");
            w.String(target.c_str(), static_cast<rapidjson::SizeType>(target.length()));
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
        },
        [this, source, target](bool ok, const rapidjson::Value& payload) {
            mAssignInFlight = false;
            if (!ok) {
                footer()->flash("Couldn't assign: " +
                                    MadJson::getString(payload, "message", "error"),
                                5000, true);
                return;
            }
            if (mOnAssigned)
                mOnAssigned(); // the target picker refreshes its "→ source" on return
            footer()->flash("Assigned \"" + source + "\" to " + target +
                                " — press B to go back.",
                            4000, false);
        },
        60000);
}

void GuiMadPageBezelSource::openSearch()
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiTextEditKeyboardPopup(
        0.0f, "Search bezels", mFilter,
        [this, alive](const std::string& s) {
            if (alive.expired())
                return;
            mFilter = s;
            populate();
        },
        false, "SEARCH"));
}

bool GuiMadPageBezelSource::input(InputConfig* config, Input input)
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

std::vector<HelpPrompt> GuiMadPageBezelSource::getHelpPrompts()
{
    return {HelpPrompt("up/down", "choose"), HelpPrompt("a", "assign"),
            HelpPrompt("y", "search"), HelpPrompt("b", "back")};
}
