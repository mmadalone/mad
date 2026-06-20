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

#include <algorithm>
#include <cctype>
#include <functional>

namespace
{
    std::string lower(std::string s)
    {
        std::transform(s.begin(), s.end(), s.begin(),
                       [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        return s;
    }
} // namespace

//  ── GuiMadPageBezelAssign (target-game picker) ──

GuiMadPageBezelAssign::GuiMadPageBezelAssign(GuiMadPanel* panel, const std::string& key,
                                             const std::string& label,
                                             const std::function<void()>& onChanged)
    : MadPage {panel, label + " — ASSIGN BEZEL"}
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
                                     MadJson::getBool(r, "has_own_bezel"),
                                     MadJson::getString(r, "title"),
                                     MadJson::getString(r, "assigned_title")});
            // keepCursor: on the post-assign refresh the cursor was just restored
            // by onRestoreFocus(); on the initial load the list cursor is 0 anyway.
            populate(/*keepCursor=*/true);
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

void GuiMadPageBezelAssign::ensureWidgets()
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
    mList->setOnSelect([this](int i) { pickSource(i); });
    addChild(mList.get());
    mList->onFocusGained();
}

void GuiMadPageBezelAssign::populate(bool keepCursor)
{
    ensureWidgets();

    const std::string f {lower(mFilter)};
    mShown.clear();
    for (const Rom& r : mRoms)
        if (f.empty() || lower(rowText(r)).find(f) != std::string::npos ||
            lower(r.game).find(f) != std::string::npos) // match title OR rom stem
            mShown.push_back(r);

    mHeader->setText("Pick a game, then choose an existing bezel for it.  " +
                     std::to_string(mShown.size()) + (f.empty() ? " games" : " matches") +
                     " · press Y to search");

    std::vector<MadVirtualList::Row> rows;
    rows.reserve(mShown.size());
    const unsigned int color {MadTheme::color(MadColor::Primary)};
    for (const Rom& r : mShown) {
        const std::string assignedDisp {r.assignedTitle.empty() ? r.assigned : r.assignedTitle};
        const std::string hint {r.assigned.empty() ? "  ·  (no bezel)" : "  →  " + assignedDisp};
        rows.push_back({rowText(r) + hint, color});
    }
    mList->setRows(rows, keepCursor);
    mPanel->refreshHelpPrompts();
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
    if (input.value != 0 && config->isMappedTo("y", input) && mList != nullptr) {
        openSearch();
        return true;
    }
    return mList != nullptr ? mList->input(config, input) : false;
}

void GuiMadPageBezelAssign::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

void GuiMadPageBezelAssign::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->cursor();
}

void GuiMadPageBezelAssign::onRestoreFocus()
{
    if (mList != nullptr)
        mList->setCursor(mFocusCookie);
}

std::vector<HelpPrompt> GuiMadPageBezelAssign::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"),
                                     HelpPrompt("a", "pick bezel"), HelpPrompt("y", "search")};
    if (mList != nullptr && mList->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    prompts.push_back(HelpPrompt("b", "back"));
    return prompts;
}

//  ── GuiMadPageBezelSource (source-bezel picker, with preview) ──

GuiMadPageBezelSource::GuiMadPageBezelSource(GuiMadPanel* panel, const std::string& key,
                                             const std::string& target,
                                             const std::function<void()>& onAssigned)
    : MadPage {panel, "PICK A BEZEL"}
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
                    mBezels.push_back({MadJson::getString(b, "name"),
                                       MadJson::getString(b, "preview"),
                                       MadJson::getString(b, "title")});
            populate();
        },
        15000);
}

void GuiMadPageBezelSource::ensureWidgets()
{
    if (mList != nullptr)
        return;
    // Reserve the right ~40% for the bezel preview pane (mirrors GuiMadPageBezelPerGame).
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
    mList->setOnSelect([this](int i) { assign(i); });
    mList->setOnCursorChanged([this](int) { updatePreview(); });
    addChild(mList.get());
    mList->onFocusGained();

    mPreview = std::make_shared<ImageComponent>();
    mPreview->setOrigin(0.5f, 0.0f);
    addChild(mPreview.get());
    const float paneLeft {mViewportPos.x + listWidth};
    const float paneWidth {mViewportSize.x - listWidth};
    mPreview->setMaxSize(paneWidth * 0.9f, mViewportSize.y * 0.6f);
    mPreview->setPosition(paneLeft + paneWidth * 0.5f, mViewportPos.y);
}

void GuiMadPageBezelSource::populate()
{
    ensureWidgets();

    const std::string f {lower(mFilter)};
    mShown.clear();
    for (const Bezel& b : mBezels)
        if (f.empty() || lower(rowText(b)).find(f) != std::string::npos ||
            lower(b.name).find(f) != std::string::npos) // match title OR bezel stem
            mShown.push_back(b);

    // Cap the (variable, possibly long arcade-stem) target so the header can't
    // wrap past the two reserved lines and draw over the list's first row.
    const std::string tgt {mTarget.size() > 40 ? mTarget.substr(0, 39) + "…" : mTarget};
    mHeader->setText("Bezel for \"" + tgt + "\" · " + std::to_string(mShown.size()) +
                     (f.empty() ? " bezels" : " matches") + " · press Y to search");

    std::vector<MadVirtualList::Row> rows;
    rows.reserve(mShown.size());
    const unsigned int color {MadTheme::color(MadColor::Primary)};
    for (const Bezel& b : mShown)
        rows.push_back({rowText(b), color});
    mList->setRows(rows, /*keepCursor=*/false);

    mPanel->refreshHelpPrompts();
    updatePreview();
}

void GuiMadPageBezelSource::updatePreview()
{
    if (mPreview == nullptr)
        return;
    const int c {mList != nullptr ? mList->cursor() : -1};
    if (c >= 0 && c < static_cast<int>(mShown.size()))
        mPreview->setImage(mShown[c].preview); // empty path renders transparent (safe)
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
    if (input.value != 0 && config->isMappedTo("y", input) && mList != nullptr) {
        openSearch();
        return true;
    }
    return mList != nullptr ? mList->input(config, input) : false;
}

void GuiMadPageBezelSource::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

void GuiMadPageBezelSource::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->cursor();
}

void GuiMadPageBezelSource::onRestoreFocus()
{
    if (mList != nullptr)
        mList->setCursor(mFocusCookie);
}

std::vector<HelpPrompt> GuiMadPageBezelSource::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"), HelpPrompt("a", "assign"),
                                     HelpPrompt("y", "search")};
    if (mList != nullptr && mList->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    prompts.push_back(HelpPrompt("b", "back"));
    return prompts;
}
