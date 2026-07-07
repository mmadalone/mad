//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageSidebar.cpp
//
//  MAD control panel: "Sidebar" section (deck-patches). See the header.
//

#include "guis/mad/pages/GuiMadPageSidebar.h"

#include "Sound.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"

GuiMadPageSidebar::GuiMadPageSidebar(GuiMadPanel* panel)
    : MadPage {panel, "SIDEBAR"}
{
}

void GuiMadPageSidebar::build()
{
    setLoadingText("Loading sidebar options…");
    requestSections();
}

void GuiMadPageSidebar::requestSections()
{
    pageRequest(
        "sidebar.sections", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load sidebar options: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            populate(payload);
        });
}

bool GuiMadPageSidebar::visibleFor(const std::string& key) const
{
    if (key == "sidebar")
        return true; // the escape hatch — never hidden
    const auto m {mMode.find(key)};
    const std::string mode {m == mMode.end() ? "auto" : m->second};
    if (mode == "hide")
        return false;
    if (mode == "show")
        return true;
    const auto core {mCore.find(key)};
    const auto cap {mCap.find(key)};
    return (core != mCore.end() && core->second) || (cap != mCap.end() && cap->second);
}

void GuiMadPageSidebar::populate(const rapidjson::Value& result)
{
    const rapidjson::Value& sections {MadJson::getMember(result, "sections")};
    if (!sections.IsArray()) {
        footer()->setStatus("Couldn't read sidebar options (backend out of date?)", true);
        return;
    }

    mKeyByLabel.clear();
    mMode.clear();
    mInitialMode.clear();
    mInitialOrder.clear();
    mCore.clear();
    mCap.clear();
    mList = nullptr;
    mApplyButton = nullptr;

    std::vector<std::string> order;
    std::vector<bool> hidden;
    for (rapidjson::SizeType i {0}; i < sections.Size(); ++i) {
        const rapidjson::Value& s {sections[i]};
        const std::string key {MadJson::getString(s, "key")};
        if (key.empty())
            continue;
        std::string label {MadJson::getString(s, "label", key)};
        if (label.empty())
            label = key;
        const bool fshow {MadJson::getBool(s, "force_show", false)};
        const bool fhide {MadJson::getBool(s, "force_hide", false)};
        // The sidebar entry is never hidden; pin its local mode to "auto" so the page never
        // disagrees with the backend (which also forces it visible) even on a hand-edited conf.
        const std::string mode {key == "sidebar" ? "auto" : (fshow ? "show" : (fhide ? "hide" : "auto"))};
        // Keep list labels unique even if two rows share a name.
        std::string uniq {label};
        int n {2};
        while (mKeyByLabel.count(uniq))
            uniq = label + " (" + std::to_string(n++) + ")";
        mKeyByLabel[uniq] = key;
        mMode[key] = mode;
        mInitialMode[key] = mode;
        mCore[key] = MadJson::getBool(s, "core", false);
        mCap[key] = MadJson::getBool(s, "capability_met", false);
        order.emplace_back(uniq);
        hidden.push_back(!visibleFor(key));
    }

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};

    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};

    mIntro = std::make_shared<TextComponent>(
        "Reorder entries (A lift, move, A drop) and show/hide each (Left/Right). Apply updates "
        "the sidebar right away. The Sidebar entry can't be hidden.",
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(0.0f, y);
    mIntro->setSize(mScroll->getSize().x * 0.92f, 0.0f);
    mScroll->addChild(mIntro.get());
    y += mIntro->getSize().y + smallHeight * 0.5f;

    mList = std::make_shared<MadReorderList>();
    mList->setPlayerTags(false);
    mList->setPosition(0.0f, y);
    mList->setSize(mViewportSize.x * 0.7f, 1.0f);
    mList->setItems(order);
    mInitialOrder = order; // clean baseline: the saved label order
    mList->setHidden(hidden);
    mList->setOnToggle([this](int i, int dir) { cycleMode(i, dir); });
    mList->setSize(mViewportSize.x * 0.7f, std::max(1.0f, mList->contentHeight()));
    mScroll->addChild(mList.get());
    y += mList->getSize().y + smallHeight * 0.5f;

    mApplyButton = std::make_shared<ButtonComponent>("APPLY", "apply", [this] { apply(); });
    mApplyButton->setPosition(0.0f, y);
    mScroll->addChild(mApplyButton.get());
    y += mApplyButton->getSize().y;

    mScroll->setContentHeight(y + smallHeight * 0.5f);
    mScroll->setScrollOffset(mScrollCookie);

    mBuilt = true;
    setFocusTarget(FocusList);
    followFocus();
    mPanel->refreshHelpPrompts();
}

void GuiMadPageSidebar::cycleMode(int index, int dir)
{
    if (mList == nullptr || index < 0 || index >= static_cast<int>(mList->items().size()))
        return;
    const std::string label {mList->items()[index]};
    const auto it {mKeyByLabel.find(label)};
    if (it == mKeyByLabel.end())
        return;
    const std::string key {it->second};
    if (key == "sidebar") {
        footer()->flash("The Sidebar page can't be hidden — it's how you get back.");
        return;
    }
    const std::string cur {mMode.count(key) ? mMode[key] : "auto"};
    // Right cycles forward (auto -> show -> hide -> auto), Left backward.
    const std::string next {dir >= 0 ? (cur == "auto" ? "show" : (cur == "show" ? "hide" : "auto"))
                                     : (cur == "auto" ? "hide" : (cur == "hide" ? "show" : "auto"))};
    mMode[key] = next;
    mList->setRowHidden(index, !visibleFor(key));
    footer()->setStatus(key + " -> " + next + " (press X to save)", false);
}

void GuiMadPageSidebar::apply()
{
    // The reordered list works in labels; map them back to section keys.
    const std::vector<std::string> labelOrder {mList->items()};
    std::vector<std::string> keys;
    for (const std::string& label : labelOrder) {
        const auto it {mKeyByLabel.find(label)};
        if (it != mKeyByLabel.end())
            keys.push_back(it->second);
    }
    pageRequest(
        "sidebar.set_order",
        [keys](MadJson::Writer& writer) {
            writer.Key("order");
            writer.StartArray();
            for (const std::string& k : keys)
                writer.String(k.c_str(), static_cast<rapidjson::SizeType>(k.length()));
            writer.EndArray();
        },
        [this, labelOrder](bool ok, const rapidjson::Value& payload) {
            if (ok) {
                mInitialOrder = labelOrder; // order baseline advances only on a confirmed write
                mPanel->refreshHelpPrompts();
            }
            else
                footer()->setStatus("Couldn't save sidebar order: " +
                                        MadJson::getString(payload, "message", "error"),
                                    true);
        });

    // Persist each changed mode (unchanged rows + the never-hidden sidebar are skipped).
    // Advance the baseline only for rows that actually saved, and surface any failure so the
    // user can re-apply that row (don't blindly commit mMode before the writes confirm).
    for (const auto& kv : mMode) {
        const std::string key {kv.first};
        const std::string mode {kv.second};
        const auto init {mInitialMode.find(key)};
        if (init != mInitialMode.end() && init->second == mode)
            continue;
        pageRequest(
            "sidebar.set",
            [key, mode](MadJson::Writer& writer) {
                writer.Key("key");
                writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
                writer.Key("mode");
                writer.String(mode.c_str(), static_cast<rapidjson::SizeType>(mode.length()));
            },
            [this, key, mode](bool ok, const rapidjson::Value& payload) {
                if (ok) {
                    mInitialMode[key] = mode; // baseline advances only on a confirmed write
                    mPanel->refreshHelpPrompts(); // x=save drops once every change is saved
                }
                else
                    footer()->setStatus("Couldn't save " + key + ": " +
                                            MadJson::getString(payload, "message", "error"),
                                        true);
            });
    }

    // Authoritative live rebuild: GuiMadPanel re-fetches sidebar.sections (queued after the
    // writes above on the same pipe, so it sees the final state) and rebuilds the sidebar in
    // place, keeping us on this page. No panel reopen needed.
    mPanel->refreshSidebarLive();
    footer()->flash("Applied.");
}

bool GuiMadPageSidebar::isDirty() const
{
    if (!mBuilt || mList == nullptr)
        return false;
    if (mList->items() != mInitialOrder)
        return true;
    for (const auto& kv : mMode) {
        const auto init {mInitialMode.find(kv.first)};
        if (init == mInitialMode.end() || init->second != kv.second)
            return true;
    }
    return false;
}

bool GuiMadPageSidebar::hasUnsavedEdits() const
{
    return isDirty();
}

bool GuiMadPageSidebar::madSave()
{
    if (!isDirty())
        return false;
    apply(); // baselines (order + per-row mode) advance in apply()'s success callbacks
    return true;
}

bool GuiMadPageSidebar::madCancel()
{
    if (!isDirty())
        return false;
    requestSections(); // re-fetch + repopulate: discards staged order + mode edits
    footer()->flash("Reverted.");
    return true;
}

void GuiMadPageSidebar::setFocusTarget(int target)
{
    mFocusTarget = target;
    if (mList != nullptr) {
        if (target == FocusList)
            mList->onFocusGained();
        else
            mList->onFocusLost();
    }
    if (mApplyButton != nullptr) {
        if (target == FocusApply)
            mApplyButton->onFocusGained();
        else
            mApplyButton->onFocusLost();
    }
    mPanel->refreshHelpPrompts();
}

void GuiMadPageSidebar::moveFocus(int target)
{
    setFocusTarget(target);
    followFocus();
}

void GuiMadPageSidebar::followFocus()
{
    if (mScroll == nullptr)
        return;
    float top {0.0f};
    float bottom {0.0f};
    if (mFocusTarget == FocusList && mList != nullptr) {
        const glm::vec2 row {mList->cursorRowRect()};
        top = mList->getPosition().y + row.x;
        bottom = mList->getPosition().y + row.y;
    }
    else if (mApplyButton != nullptr) {
        top = mApplyButton->getPosition().y;
        bottom = top + mApplyButton->getSize().y;
    }
    mScroll->ensureVisible(top, bottom);
}

bool GuiMadPageSidebar::onBackPressed()
{
    if (mList != nullptr && mList->carrying()) {
        mList->cancelCarry();
        mPanel->refreshHelpPrompts();
        return true;
    }
    return false;
}

bool GuiMadPageSidebar::input(InputConfig* config, Input input)
{
    if (!mBuilt)
        return false;

    if (mFocusTarget == FocusList) {
        if (mList->input(config, input)) {
            followFocus();                // cursor (or the carried row) moved
            mPanel->refreshHelpPrompts(); // carry/toggle state changes the prompts
            return true;
        }
        if (input.value == 0)
            return false;
        if (config->isMappedLike("down", input)) {
            if (!mList->carrying())       // a carry never leaves the list
                moveFocus(FocusApply);
            return true;
        }
        if (config->isMappedLike("up", input))
            return true;                  // top edge
        return false;
    }

    // Apply button focused.
    if (input.value == 0)
        return false;
    if (config->isMappedLike("up", input)) {
        moveFocus(FocusList);
        return true;
    }
    if (config->isMappedLike("down", input))
        return true;                      // bottom edge
    if (config->isMappedTo("a", input))
        return mApplyButton->input(config, input);
    return false;
}

void GuiMadPageSidebar::pageScroll(int direction)
{
    if (!mBuilt || mScroll == nullptr || !mScroll->overflows())
        return;
    if (mScroll->pageScroll(direction))
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
}

std::vector<HelpPrompt> GuiMadPageSidebar::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (!mBuilt)
        return prompts;
    if (mFocusTarget == FocusList && mList != nullptr) {
        prompts = mList->getHelpPrompts();
    }
    else {
        prompts.push_back(HelpPrompt("a", "apply"));
        prompts.push_back(HelpPrompt("up/down", "choose"));
    }
    if (isDirty()) {
        prompts.push_back(HelpPrompt("x", "save"));
        prompts.push_back(HelpPrompt("y", "cancel"));
    }
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void GuiMadPageSidebar::onSaveFocus()
{
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void GuiMadPageSidebar::onRestoreFocus()
{
    if (!mBuilt)
        return;
    setFocusTarget(mFocusTarget);
    if (mScroll != nullptr)
        mScroll->setScrollOffset(mScrollCookie);
    followFocus();
}
