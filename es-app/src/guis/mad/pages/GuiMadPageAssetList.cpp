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

using MadPageUtil::humanSize;

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

long long GuiMadPageAssetList::assetSelectedSize(const Asset& a) const
{
    // All-kinds (or never drilled) deliberately returns a.size: act() sends the coarse
    // "media" key there, so the tally mirrors the upload branch-for-branch.
    if (a.key != "media" || !mMediaDrilled || mMediaKinds.size() == mMediaKindKeys.size())
        return a.size;
    long long total {0};
    for (const std::string& k : mMediaKinds) {
        const auto it {mMediaKindSizes.find(k)};
        if (it != mMediaKindSizes.end())
            total += it->second;
    }
    return total;
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
                    asset.sizePartial = MadJson::getBool(a, "size_partial", false);
                    // Pre-tick everything present ("back up all" = one X); on a BACKUP the durable
                    // root remembers what was unticked here before, so re-opening a game shows the
                    // same picture the games list is totalling.
                    asset.selected = asset.present &&
                                     (mRestore || mRoot == nullptr ||
                                      mRoot->assetTicked(mSystem, mStem, asset.key));
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
    // EVERY present asset gets its own line with its size, not just the focused one (user 2026-07-31,
    // mocked): the point of the panel is to see at a glance what this game is made of and what each
    // part costs, which a one-row-at-a-time readout cannot give you.
    std::vector<std::string> lines;
    for (const Asset& a : mAssets) {
        if (!a.present)
            continue;
        // A drilled Media row shows the TICKED kinds' size, matching what X sends.
        std::string line {a.label + ":  " + humanSize(assetSelectedSize(a))};
        if (a.key == "media" && mMediaDrilled)
            line += "  ·  " + std::to_string(static_cast<int>(mMediaKinds.size())) + " of " +
                    std::to_string(static_cast<int>(mMediaKindKeys.size())) + " kinds";
        else if (a.count > 1)
            line += "  ·  " + std::to_string(a.count) + " Files";
        lines.push_back(line);
    }
    // The FOCUSED row's note still rides along (a prefix shared by several games says so there, and
    // that warning is the reason the field exists) - it is about one row, so it follows the list.
    const int c {mList != nullptr ? mList->cursor() : -1};
    if (c >= 0 && c < static_cast<int>(mAssets.size()) && !mAssets[c].detail.empty())
        lines.push_back(mAssets[c].detail);

    long long total {0};
    for (const Asset& a : mAssets)
        if (rowSelected(a))
            total += assetSelectedSize(a);

    // TextComponent does not clip vertically, so keep the block inside its band: drop the least
    // important lines (from the bottom of the asset list) rather than spilling off the viewport.
    const float lineHeight {std::max(1.0f, Font::get(FONT_SIZE_SMALL)->getHeight())};
    const int fits {static_cast<int>(mDetail->getSize().y / lineHeight)};
    const int budget {std::max(1, fits - 2)};      // blank + "Selected:" always reserved
    std::string text;
    for (int i {0}; i < static_cast<int>(lines.size()); ++i) {
        if (i >= budget) {
            text += "+ " + std::to_string(static_cast<int>(lines.size()) - i) + " more\n";
            break;
        }
        text += lines[i] + "\n";
    }
    text += "\nSelected:  " + humanSize(total);
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
        // Write through to the durable root: these ticks are what the per-system BACK UP totals
        // and copies, so they must outlive this page. Only EXCLUSIONS are stored (see mAssetOff).
        if (!mRestore && mRoot != nullptr) {
            if (a.selected)
                mRoot->mAssetOff[mSystem][mStem].erase(a.key);
            else
                mRoot->mAssetOff[mSystem][mStem].insert(a.key);
        }
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

void GuiMadPageAssetList::beginMediaDrill(
    const std::vector<std::pair<std::string, long long>>& presentKinds)
{
    mMediaKindKeys.clear();
    mMediaKindSizes.clear();
    for (const auto& kind : presentKinds) {
        mMediaKindKeys.push_back(kind.first);
        mMediaKindSizes[kind.first] = kind.second;
    }
    if (!mMediaDrilled) {
        mMediaDrilled = true;
        // seed from the coarse Media tick: ticked -> all kinds; unticked -> none.
        const int mi {mediaIndex()};
        const bool wasOn {mi >= 0 && mAssets[mi].selected};
        mMediaKinds.clear();
        if (wasOn)
            for (const std::string& k : mMediaKindKeys)
                mMediaKinds.insert(k);
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
    // The Selected total (same math the under-art panel shows) rides along so a big MEGA
    // upload can confirm with the real number; approx = the backend's sizing budget ran
    // out somewhere, so the total is a floor.
    long long total {0};
    bool approx {false};
    for (const Asset& a : mAssets)
        if (rowSelected(a)) {
            total += assetSelectedSize(a);
            if (a.sizePartial)
                approx = true;
        }
    // startGameAssets sends the backup to the bar's destination; it claims the run only when the
    // op actually fires, and a big cloud selection confirms first.
    mRoot->startGameAssets(mSystem, mStem, keys, total, approx);
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
