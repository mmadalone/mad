//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBezelReviewQueue.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageBezelReviewQueue.h"

#include "Window.h"
#include "guis/GuiTextEditKeyboardPopup.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadPageUtil.h"
#include "guis/mad/MadTheme.h"

#include <string>

GuiMadPageBezelReviewQueue::GuiMadPageBezelReviewQueue(GuiMadPanel* panel, const std::string& key,
                                                       const std::string& label,
                                                       const std::function<void()>& onChanged)
    : MadPage {panel, label + " — REVIEW BEZELS"}
    , mKey {key}
    , mLabel {label}
    , mOnChanged {onChanged}
{
}

void GuiMadPageBezelReviewQueue::build()
{
    setLoadingText("Finding games without a bezel…");
    const std::string key {mKey};
    pageRequest(
        "bezels.fuzzy_review",
        [key](MadJson::Writer& w) {
            w.Key("key");
            w.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                setLoadingText("");
                footer()->setStatus("Couldn't open the review: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mAuto = MadJson::getInt(payload, "auto", 0);
            if (mAuto > 0 && mOnChanged)
                mOnChanged(); // the normalized-equal auto-wire already changed this system

            mRoms.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "roms")};
            if (arr.IsArray())
                for (const rapidjson::Value& r : arr.GetArray())
                    mRoms.push_back({MadJson::getString(r, "game"), MadJson::getString(r, "title")});

            if (mRoms.empty()) {
                setLoadingText(mAuto > 0
                                   ? "Auto-assigned " + std::to_string(mAuto) +
                                         " bezel(s). No others need review — press B to go back."
                                   : "Every game already has a bezel — nothing to review. "
                                     "Press B to go back.");
                mPanel->refreshHelpPrompts();
                return;
            }
            setLoadingText("");
            ensureWidgets();
            showRom(0);
        },
        120000);
}

void GuiMadPageBezelReviewQueue::ensureWidgets()
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
    mList->setOnSelect([this](int) { assignSelected(); });
    mList->setOnCursorChanged([this](int) { updatePreview(); });
    addChild(mList.get());
    mList->onFocusGained();

    mPreview = MadPageUtil::makeBezelPreview(mViewportPos, mViewportSize, listWidth);
    addChild(mPreview.get());
}

void GuiMadPageBezelReviewQueue::showRom(int idx)
{
    mIdx = idx;
    if (idx >= static_cast<int>(mRoms.size())) {
        finish(); // all ROMs reviewed — show the done state (does not pop)
        return;
    }
    mQuery.clear(); // each ROM starts from its own ranked candidates; refine is per-ROM
    loadCandidates();
}

void GuiMadPageBezelReviewQueue::loadCandidates()
{
    const Rom& r {mRoms[mIdx]};
    const std::string disp {r.title.empty() ? r.game : r.title};
    // Cap the (variable, possibly long arcade) name so the header can't wrap past its two
    // reserved lines and overdraw the list (mirrors GuiMadPageBezelSource).
    const std::string shown {disp.size() > 40 ? disp.substr(0, 39) + "…" : disp};
    const std::string prefix {"(" + std::to_string(mIdx + 1) + " of " +
                              std::to_string(mRoms.size()) + ")  "};
    mHeader->setText(mQuery.empty()
                         ? prefix + shown + "  —  pick a bezel  ·  Y search  ·  X skips"
                         : prefix + shown + "  —  search: \"" + mQuery + "\"  ·  X skips");

    // Clear the list while the candidates load (lazy — ranking is on demand).
    mCands.clear();
    mList->setRows({}, /*keepCursor=*/false);

    const std::string key {mKey};
    const std::string game {r.game};
    const std::string query {mQuery};
    pageRequest(
        "bezels.fuzzy_candidates",
        [key, game, query](MadJson::Writer& w) {
            w.Key("key");
            w.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            w.Key("game");
            w.String(game.c_str(), static_cast<rapidjson::SizeType>(game.length()));
            if (!query.empty()) {
                w.Key("query");
                w.String(query.c_str(), static_cast<rapidjson::SizeType>(query.length()));
            }
        },
        [this, game, query, prefix, shown](bool ok, const rapidjson::Value& payload) {
            // Ignore a stale reply: the user skipped to another ROM, or refined again.
            if (mIdx >= static_cast<int>(mRoms.size()) || mRoms[mIdx].game != game ||
                mQuery != query)
                return;
            if (!ok) {
                footer()->flash("Couldn't load candidates: " +
                                    MadJson::getString(payload, "message", "error"),
                                4000, true);
                return;
            }
            mCands.clear();
            std::vector<MadVirtualList::Row> rows;
            const rapidjson::Value& arr {MadJson::getMember(payload, "candidates")};
            if (arr.IsArray()) {
                for (const rapidjson::Value& c : arr.GetArray()) {
                    const std::string name {MadJson::getString(c, "name")};
                    const std::string title {MadJson::getString(c, "title")};
                    const std::string preview {MadJson::getString(c, "preview")};
                    mCands.push_back({name, title, preview});
                    const rapidjson::Value& sc {MadJson::getMember(c, "score")};
                    const int pct {sc.IsNumber() ? static_cast<int>(sc.GetDouble() * 100.0 + 0.5)
                                                 : 0};
                    rows.push_back({(title.empty() ? name : title) + "   ·  " +
                                        std::to_string(pct) + "%",
                                    MadTheme::color(MadColor::Primary)});
                }
            }
            if (mCands.empty())
                mHeader->setText(query.empty()
                                     ? prefix + shown + "  —  no close match; Y to search, X to skip"
                                     : prefix + shown + "  —  no match for \"" + query +
                                           "\"; Y to retry, X to skip");
            mList->setRows(rows, /*keepCursor=*/false);
            updatePreview();
        },
        30000);
}

void GuiMadPageBezelReviewQueue::openRefine()
{
    if (mList == nullptr || mDone || mRoms.empty() || mAssignInFlight)
        return; // not while an assign is settling (mirrors skipRom)
    std::weak_ptr<int> alive {pageAlive()};
    const Rom& r {mRoms[mIdx]};
    const std::string disp {r.title.empty() ? r.game : r.title};
    mWindow->pushGui(new GuiTextEditKeyboardPopup(
        0.0f, "Search a bezel for: " + disp, mQuery,
        [this, alive](const std::string& s) {
            if (alive.expired())
                return;
            // Trim so an all-whitespace entry is a true no-op (reverts to ROM-name ranking),
            // keeping mQuery.empty() an honest "is this a search?" test downstream.
            const std::string::size_type b {s.find_first_not_of(" \t")};
            mQuery = (b == std::string::npos) ? ""
                                              : s.substr(b, s.find_last_not_of(" \t") - b + 1);
            loadCandidates(); // re-rank the SAME ROM's candidates against the typed text
        },
        false, "SEARCH"));
}

void GuiMadPageBezelReviewQueue::skipRom()
{
    if (mAssignInFlight || mRoms.empty())
        return;
    showRom(mIdx + 1);
}

void GuiMadPageBezelReviewQueue::assignSelected()
{
    if (mList == nullptr || mCands.empty() || mAssignInFlight)
        return;
    const int c {mList->cursor()};
    if (c < 0 || c >= static_cast<int>(mCands.size()))
        return;
    mAssignInFlight = true;
    // Mark the detail page for refresh at ISSUE time: the server write persists even if the
    // user presses B before the reply (which drops the callback below). A redundant rebuild
    // on a rare server-rejected assign is harmless — it just re-reads bezels.status.
    if (mOnChanged)
        mOnChanged();
    const std::string key {mKey};
    const std::string target {mRoms[mIdx].game};
    const std::string source {mCands[c].name};
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
        [this, source](bool ok, const rapidjson::Value& payload) {
            mAssignInFlight = false;
            if (!ok) {
                footer()->flash("Couldn't assign: " + MadJson::getString(payload, "message", "error"),
                                5000, true);
                return;
            }
            footer()->flash("Assigned " + source + ".", 2000, false);
            showRom(mIdx + 1); // advance to the next ROM (finish() shows the done state at the end)
        },
        60000);
}

void GuiMadPageBezelReviewQueue::updatePreview()
{
    if (mPreview == nullptr)
        return;
    const int c {mList != nullptr ? mList->cursor() : -1};
    if (c >= 0 && c < static_cast<int>(mCands.size()))
        mPreview->setImage(mCands[c].preview); // empty path renders transparent (safe)
    else
        mPreview->setImage("");
}

void GuiMadPageBezelReviewQueue::finish()
{
    // Don't self-pop: GuiMadPanel::popPage destroys the page immediately, and finish() is
    // reachable synchronously from input() (skipRom). Show a done state and let the user
    // press B — the panel pops us safely from its own input frame.
    mDone = true;
    mCands.clear();
    if (mPreview != nullptr)
        mPreview->setImage(""); // don't leave the last ROM's bezel lingering on the done screen
    if (mHeader != nullptr)
        mHeader->setText(mAuto > 0 ? "Review complete — " + std::to_string(mAuto) +
                                         " auto-assigned earlier.  Press B to go back."
                                   : "Review complete.  Press B to go back.");
    if (mList != nullptr)
        mList->setRows({}, /*keepCursor=*/false);
    footer()->flash("Review complete — press B to go back.", 4000, false);
    mPanel->refreshHelpPrompts();
}

bool GuiMadPageBezelReviewQueue::input(InputConfig* config, Input input)
{
    if (mDone || mList == nullptr)
        return false; // review finished (or still loading) — let B pop the page
    if (input.value != 0 && config->isMappedTo("y", input)) {
        openRefine(); // type a search to re-rank this ROM's candidates (ES-DE scraper "refine")
        return true;
    }
    if (input.value != 0 && config->isMappedTo("x", input) && !mRoms.empty()) {
        skipRom();
        return true;
    }
    return mList->input(config, input);
}

void GuiMadPageBezelReviewQueue::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

void GuiMadPageBezelReviewQueue::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->cursor();
}

void GuiMadPageBezelReviewQueue::onRestoreFocus()
{
    if (mList != nullptr)
        mList->setCursor(mFocusCookie);
}

std::vector<HelpPrompt> GuiMadPageBezelReviewQueue::getHelpPrompts()
{
    if (mDone || mRoms.empty())
        return {HelpPrompt("b", "back")};
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"), HelpPrompt("a", "assign"),
                                     HelpPrompt("y", "search"), HelpPrompt("x", "skip")};
    if (mList != nullptr && mList->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    prompts.push_back(HelpPrompt("b", "exit"));
    return prompts;
}
