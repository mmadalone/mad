//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageAssetList.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageAssetList.h"

#include "Window.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadPageUtil.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackupRestore.h"
#include "guis/mad/pages/GuiMadPageMediaKinds.h" // the per-kind media drill leaf (Y on the Media row)
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

GuiMadPageAssetList::GuiMadPageAssetList(GuiMadPanel* panel, GuiMadPageBackupRestore* root,
                                        const std::string& source, const std::string& system,
                                        const std::string& stem, const std::string& name,
                                        const std::string& art, bool restore)
    : MadPage {panel, name.empty() ? stem : name}
    , mRoot {root}
    , mSource {source}
    , mSystem {system}
    , mStem {stem}
    , mName {name.empty() ? stem : name}
    , mArt {art}
    , mRestore {restore}
{
}

bool GuiMadPageAssetList::rowSelected(const Asset& a) const
{
    if (!a.present)
        return false;
    if (a.key == "media" && mMediaDrilled)
        return !mMediaKinds.empty(); // once drilled, the per-kind set governs the Media row
    return a.selected;
}

std::string GuiMadPageAssetList::rowGlyph(const Asset& a) const
{
    if (!a.present)
        return "- ";
    if (a.key == "media" && mMediaDrilled) {
        if (mMediaKinds.empty())
            return "○ ";
        // half-filled when a SUBSET of the game's kinds is ticked, full when all are.
        return mMediaKinds.size() == mMediaKindKeys.size() ? "● " : "◐ ";
    }
    return a.selected ? "● " : "○ ";
}

unsigned int GuiMadPageAssetList::rowColor(const Asset& a) const
{
    if (!a.present)
        return MadTheme::color(MadColor::Secondary);
    return rowSelected(a) ? MadTheme::color(MadColor::Primary) : MadTheme::color(MadColor::Secondary);
}

std::string GuiMadPageAssetList::rowText(const Asset& a) const
{
    // ROWS ARE LABELS ONLY (user 2026-07-30): sizes / file counts / media-kind state
    // moved to the under-art detail panel (updateDetail), and the old inline
    // "(Y: pick kinds)" hint is covered by the footer's MEDIA KINDS prompt. An absent
    // asset keeps its "none" marker; a NOTE row is a sentence, never "missing".
    if (!a.present && a.key != "note")
        return a.label + "   none";
    return a.label;
}

std::string GuiMadPageAssetList::headerText() const
{
    int present {0}, selected {0};
    for (const Asset& a : mAssets) {
        if (a.present)
            ++present;
        if (rowSelected(a))
            ++selected;
    }
    // The X (back up / restore) hint lives in the footer help row now; keep only the count here. The Media
    // row's "(Y: pick kinds)" stays row-local (rowText) because it is contextual to that row only.
    return mName + "  ·  " + std::to_string(selected) + " of " + std::to_string(present) + " selected";
}

void GuiMadPageAssetList::build()
{
    setLoadingText("Reading this game…");
    const std::string source {mSource};
    const std::string system {mSystem};
    const std::string stem {mStem};
    pageRequest(
        "granular.game_assets",
        [source, system, stem](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
            w.Key("system");
            w.String(system.c_str(), static_cast<rapidjson::SizeType>(system.length()));
            w.Key("game");
            w.String(stem.c_str(), static_cast<rapidjson::SizeType>(stem.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't read this game: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mAssets.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "assets")};
            if (arr.IsArray())
                for (const rapidjson::Value& a : arr.GetArray()) {
                    Asset asset;
                    asset.key = MadJson::getString(a, "key");
                    asset.label = MadJson::getString(a, "label");
                    asset.category = MadJson::getString(a, "category");
                    asset.detail = MadJson::getString(a, "detail");
                    asset.present = MadJson::getBool(a, "present");
                    asset.size = MadJson::getInt64(a, "size", 0); // 64-bit: a multi-GB game folder
                    asset.count = MadJson::getInt(a, "count", 0);
                    asset.selected = asset.present; // pre-tick everything present ("back up all" = one X)
                    mAssets.push_back(asset);
                }
            populate();
        },
        12000);
}

void GuiMadPageAssetList::ensureWidgets()
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
    mList->setOnSelect([this](int i) { toggleAt(i); });
    addChild(mList.get());
    mList->onFocusGained();

    mPreview = MadPageUtil::makeBezelPreview(mViewportPos, mViewportSize, listWidth);
    mPreview->setImage(mArt); // the game's box art, static for the page
    addChild(mPreview.get());

    // UNDER the box art: the focused row's size / file count / note plus the selected
    // total - the info the rows used to carry inline (user 2026-07-30: rows stay short).
    const float paneLeft {mViewportPos.x + listWidth};
    const float paneWidth {mViewportSize.x - listWidth};
    const float detailTop {mViewportPos.y + mViewportSize.y * 0.6f +
                           Font::get(FONT_SIZE_SMALL)->getHeight() * 0.5f};
    mDetail = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                              MadTheme::color(MadColor::Secondary),
                                              ALIGN_CENTER, ALIGN_TOP, glm::ivec2 {0, 0});
    mDetail->setPosition(paneLeft + paneWidth * 0.05f, detailTop);
    mDetail->setSize(paneWidth * 0.9f,
                     std::max(0.0f, mViewportPos.y + mViewportSize.y - detailTop));
    addChild(mDetail.get());
}

void GuiMadPageAssetList::updateDetail()
{
    if (mDetail == nullptr)
        return;
    std::string text;
    const int c {mList != nullptr ? mList->cursor() : -1};
    if (c >= 0 && c < static_cast<int>(mAssets.size())) {
        const Asset& a {mAssets[c]};
        if (a.present) {
            std::string line {humanSize(a.size)};
            if (a.key == "media" && mMediaDrilled)
                line += "  ·  " + std::to_string(static_cast<int>(mMediaKinds.size())) +
                        " of " + std::to_string(static_cast<int>(mMediaKindKeys.size())) +
                        " kinds";
            else if (a.count > 1)
                line += "  ·  " + std::to_string(a.count) + " files";
            text = line;
        }
        if (!a.detail.empty())
            text += (text.empty() ? "" : "\n") + a.detail;
    }
    long long total {0};
    for (const Asset& a : mAssets)
        if (rowSelected(a))
            total += a.size; // drilled media counts fully - close enough for the tally
    text += (text.empty() ? "" : "\n\n");
    text += "Selected:  " + humanSize(total);
    mDetail->setText(text);
}

void GuiMadPageAssetList::populate()
{
    ensureWidgets();
    mHeader->setText(headerText());
    std::vector<MadVirtualList::Row> rows;
    rows.reserve(mAssets.size());
    for (const Asset& a : mAssets)
        rows.push_back({rowGlyph(a) + rowText(a), rowColor(a)});
    if (rows.empty())
        rows.push_back({mRestore ? "Nothing to restore for this game." : "Nothing to back up for this game.",
                        MadTheme::color(MadColor::Secondary)});
    mList->setRows(rows, /*keepCursor=*/false);
    mList->setOnCursorChanged([this](int) { updateDetail(); });
    updateDetail();
    mPanel->refreshHelpPrompts();
}

void GuiMadPageAssetList::toggleAt(int i)
{
    if (i < 0 || i >= static_cast<int>(mAssets.size()))
        return;
    Asset& a {mAssets[i]};
    if (!a.present) {
        // deck-patches: an informational NOTE row's label IS a full sentence (the steam
        // "Runs via Lutris…" line), so the generic missing-asset phrasing would read as
        // nonsense - show the sentence itself.
        footer()->flash(a.key == "note" ? a.label : "This game has no " + a.label + ".", 2500,
                        false);
        return;
    }
    if (a.key == "media" && mMediaDrilled) {
        // A on a drilled Media row toggles ALL its kinds on/off; Y re-opens the per-kind picker.
        if (mMediaKinds.empty())
            mMediaKinds.insert(mMediaKindKeys.begin(), mMediaKindKeys.end());
        else
            mMediaKinds.clear();
    }
    else {
        a.selected = !a.selected;
    }
    if (i < mList->size())
        mList->setRow(i, rowGlyph(a) + rowText(a), rowColor(a));
    if (mHeader != nullptr)
        mHeader->setText(headerText());
    updateDetail(); // the Selected total changed
}

int GuiMadPageAssetList::mediaIndex() const
{
    for (int i = 0; i < static_cast<int>(mAssets.size()); ++i)
        if (mAssets[i].key == "media")
            return i;
    return -1;
}

void GuiMadPageAssetList::beginMediaDrill(const std::vector<std::string>& presentKeys)
{
    mMediaKindKeys = presentKeys;
    if (!mMediaDrilled) {
        mMediaDrilled = true;
        // seed from the coarse Media tick: ticked -> all kinds; unticked -> none.
        const int mi {mediaIndex()};
        const bool wasOn {mi >= 0 && mAssets[mi].selected};
        mMediaKinds.clear();
        if (wasOn)
            mMediaKinds.insert(presentKeys.begin(), presentKeys.end());
    }
}

void GuiMadPageAssetList::openMediaDrill()
{
    const int mi {mediaIndex()};
    if (mi < 0 || !mAssets[mi].present) {
        footer()->flash("This game has no media to pick from.", 2500, false);
        return;
    }
    // The drill fetches the game's media kinds (granular.game_media) + ticks into mMediaKinds.
    mPanel->pushPage(new GuiMadPageMediaKinds(mPanel, this, mSource, mSystem, mStem));
}

void GuiMadPageAssetList::refreshMediaRow()
{
    const int mi {mediaIndex()};
    if (mi < 0)
        return;
    if (mList != nullptr && mi < mList->size())
        mList->setRow(mi, rowGlyph(mAssets[mi]) + rowText(mAssets[mi]), rowColor(mAssets[mi]));
    if (mHeader != nullptr)
        mHeader->setText(headerText());
    updateDetail(); // the drill changed the media kinds (and so the total)
}

void GuiMadPageAssetList::act()
{
    if (mRoot == nullptr)
        return;
    if (mRoot->busy()) {
        footer()->flash("A backup or restore is already running — let it finish first.", 4000, true);
        return;
    }
    std::vector<std::string> keys;
    for (const Asset& a : mAssets) {
        if (!a.present)
            continue;
        if (a.key == "media") {
            if (!rowSelected(a))
                continue;
            // all kinds (or not drilled) -> the coarse "media" key (backs up/restores everything);
            // a subset -> per-kind "media.<kind>" keys.
            if (!mMediaDrilled || mMediaKinds.size() == mMediaKindKeys.size())
                keys.push_back("media");
            else
                for (const std::string& k : mMediaKinds)
                    keys.push_back("media." + k);
        }
        else if (a.selected) {
            keys.push_back(a.key);
        }
    }
    if (keys.empty()) {
        footer()->flash(std::string("Pick at least one thing to ") + (mRestore ? "restore" : "back up") +
                            " (press A to tick it).",
                        3500, false);
        return;
    }
    if (mRestore) {
        // one game, its ticked asset groups -> the durable root runs the rule-5 restore (preview-warned).
        mRoot->restoreAssets({{mSystem, mStem, keys}});
        return;
    }
    // startGameAssets opens a destination chooser (ON THIS DECK / MEGA CLOUD); it does NOT start the
    // backup yet, so no "Backing up…" status here - the chosen branch sets it when the op fires. The
    // chooser dialog captures input, so a second X can't slip past while it is up.
    mRoot->startGameAssets(mSystem, mStem, keys);
}

bool GuiMadPageAssetList::input(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("x", input)) {
        act();
        return true;
    }
    if (input.value != 0 && config->isMappedTo("y", input)) {
        openMediaDrill(); // pick which media kinds (box art / marquee / ...) to include
        return true;
    }
    return mList != nullptr ? mList->input(config, input) : false;
}

bool GuiMadPageAssetList::consumesSectionNav()
{
    // Leaving during a job is allowed now (the durable root's op keeps running on the daemon), so the
    // shoulder section-switch no longer needs to be blocked here either.
    return false;
}

void GuiMadPageAssetList::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

void GuiMadPageAssetList::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->cursor();
}

void GuiMadPageAssetList::onRestoreFocus()
{
    refreshMediaRow(); // returning from the media-kind drill: its per-kind selection may have changed
    if (mList != nullptr)
        mList->setCursor(mFocusCookie);
}

std::vector<HelpPrompt> GuiMadPageAssetList::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"), HelpPrompt("a", "tick"),
                                     HelpPrompt("x", mRestore ? "restore" : "back up")};
    const int mi {mediaIndex()};
    if (mi >= 0 && mAssets[mi].present)
        prompts.push_back(HelpPrompt("y", "media kinds"));
    if (mList != nullptr && mList->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    prompts.push_back(HelpPrompt("b", "back"));
    return prompts;
}
