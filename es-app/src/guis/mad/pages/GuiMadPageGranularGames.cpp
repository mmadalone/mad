//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageGranularGames.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageGranularGames.h"

#include "Window.h"
#include "guis/GuiTextEditKeyboardPopup.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadPageUtil.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackupRestore.h"
#include "utils/StringUtil.h"

#include <algorithm>
#include <functional>
#include <utility>

GuiMadPageGranularGames::GuiMadPageGranularGames(GuiMadPanel* panel, GuiMadPageBackupRestore* root,
                                                 const std::string& category,
                                                 const std::string& source, const std::string& mode,
                                                 const std::string& system,
                                                 const std::string& systemLabel,
                                                 std::set<std::string>* selectionSink)
    : MadPage {panel, systemLabel}
    , mRoot {root}
    , mCategory {category}
    , mSource {source}
    , mMode {mode}
    , mSystem {system}
    , mLabel {systemLabel}
    , mBackup {mode == "backup"}
    , mSelectionSink {selectionSink}
{
}

std::string GuiMadPageGranularGames::rowGlyph(const Game& game) const
{
    if (!game.present)
        return "⚠ ";
    return game.selected ? "● " : "○ ";
}

unsigned int GuiMadPageGranularGames::rowColor(const Game& game) const
{
    if (!game.present)
        return MadTheme::color(MadColor::Red);        // "ROM missing" — dimmed, not selectable
    return game.selected ? MadTheme::color(MadColor::Primary)
                         : MadTheme::color(MadColor::Secondary);
}

std::string GuiMadPageGranularGames::headerText() const
{
    // Button hints (search / open / restore / assets) live in the footer help row now; keep only counts.
    const std::string count {std::to_string(mShown.size()) + (mFilter.empty() ? " games" : " matches")};
    int selected {0};
    for (const Game& game : mGames)  // count this system's ticked games (the sink is cross-system)
        if (game.selected)
            ++selected;
    return count + " · " + std::to_string(selected) + " selected";
}

void GuiMadPageGranularGames::build()
{
    setLoadingText("Loading games…");
    const std::string source {mSource};
    const std::string category {mCategory};
    const std::string system {mSystem};
    pageRequest(
        "granular.browse",
        [source, category, system](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
            w.Key("category");
            w.String(category.c_str(), static_cast<rapidjson::SizeType>(category.length()));
            w.Key("system");
            w.String(system.c_str(), static_cast<rapidjson::SizeType>(system.length()));
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
            const rapidjson::Value& arr {MadJson::getMember(payload, "items")};
            if (arr.IsArray())
                for (const rapidjson::Value& g : arr.GetArray()) {
                    Game game;
                    game.id = MadJson::getString(g, "id");
                    game.stem = MadJson::getString(g, "stem");
                    if (game.stem.empty()) {  // a restore (manifest) item may omit stem — derive from id
                        const std::string::size_type colon {game.id.find(':')};
                        game.stem = colon == std::string::npos ? game.id : game.id.substr(colon + 1);
                    }
                    game.name = MadJson::getString(g, "name");
                    game.art = MadJson::getString(g, "art");
                    // has_rom is present only on a LIVE (backup) browse; a restore browse lists only
                    // games that ARE in the backup, so treat a missing flag as present.
                    game.present = g.HasMember("has_rom") ? MadJson::getBool(g, "has_rom") : true;
                    // in SELECT mode, seed each row's tick from the cross-system cart so re-entering a
                    // system shows what is already chosen.
                    // SELECT mode seeds from the cross-system cart; the BACKUP browse starts with
                    // everything ticked and remembers only what the user unticked (mRoot->mGameOff).
                    game.selected = mSelectionSink != nullptr
                                        ? mSelectionSink->count(game.id) > 0
                                        : (mBackup && mRoot != nullptr
                                               ? mRoot->gameTicked(mSystem, game.stem)
                                               : mSelectionSink != nullptr);
                    mGames.push_back(game);
                }
            populate();
        },
        8000);
}

void GuiMadPageGranularGames::ensureWidgets()
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
    mList->setOnSelect([this](int i) { activateAt(i); });
    mList->setOnCursorChanged([this](int) {
        updatePreview();
        updateDetail();   // the panel shows the FOCUSED game's assets
    });
    addChild(mList.get());
    mList->onFocusGained();

    mPreview = MadPageUtil::makeBezelPreview(mViewportPos, mViewportSize, listWidth);
    addChild(mPreview.get());

    // UNDER the box art: what is ticked, each size, and the total (user 2026-07-31). Same band and
    // same construction as GuiMadPageAssetList's detail panel, so the two pages read alike.
    if (sizesApply()) {
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
}

// ---- exact per-system sizing (backup browse) ---------------------------------------------------
// The totals have to be EXACT, and exact is slow on a big system (measured: 51 s for fba's 1828
// games), so granular.system_sizes streams: the panel fills in as games arrive and says how far it
// has got. The backend caches per game, so this is paid once and every later tick is arithmetic.
void GuiMadPageGranularGames::startSystemSizes()
{
    if (!mBackup || mSizesStreaming || mDetail == nullptr)
        return;
    mSizesStreaming = true;
    mSizedN = 0;
    mSizeTotalN = 0;
    const std::string system {mSystem};
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.system_sizes",
        [system](MadJson::Writer& w) {
            w.Key("system");
            w.String(system.c_str(), static_cast<rapidjson::SizeType>(system.length()));
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (!ok) {
                mSizesStreaming = false;
                updateDetail();
                return;
            }
            mSizeStreamToken = MadJson::getString(payload, "stream");
            backend()->setStreamCallback(
                mSizeStreamToken, [this, alive](const rapidjson::Value& data) {
                    if (alive.expired())
                        return;
                    const rapidjson::Value& games {MadJson::getMember(data, "games")};
                    if (games.IsArray())
                        for (const rapidjson::Value& g : games.GetArray()) {
                            std::vector<AssetSize> assets;
                            const rapidjson::Value& arr {MadJson::getMember(g, "assets")};
                            if (arr.IsArray())
                                for (const rapidjson::Value& a : arr.GetArray())
                                    assets.push_back(
                                        {MadJson::getString(a, "key"), MadJson::getString(a, "label"),
                                         MadJson::getInt64(a, "size", 0),
                                         static_cast<int>(MadJson::getInt64(a, "count", 0))});
                            mGameAssets[MadJson::getString(g, "stem")] = assets;
                        }
                    if (data.HasMember("done_n")) {
                        mSizedN = static_cast<int>(MadJson::getInt64(data, "done_n", 0));
                        mSizeTotalN = static_cast<int>(MadJson::getInt64(data, "total_n", 0));
                    }
                    if (MadJson::getBool(data, "done") || MadJson::getBool(data, "closed")) {
                        mSizesStreaming = false;
                        if (!mSizeStreamToken.empty()) {
                            backend()->clearStreamCallback(mSizeStreamToken);
                            mSizeStreamToken.clear();
                        }
                    }
                    updateDetail();
                });
        },
        20000);
}

long long GuiMadPageGranularGames::gameTickedSize(const std::string& stem) const
{
    const auto it = mGameAssets.find(stem);
    if (it == mGameAssets.end() || mRoot == nullptr)
        return 0;
    long long total {0};
    for (const AssetSize& a : it->second)
        if (mRoot->assetTicked(mSystem, stem, a.key))
            total += a.size;
    return total;
}

long long GuiMadPageGranularGames::tickedTotal() const
{
    long long total {0};
    for (const Game& game : mGames)          // mGames, not mShown: a filtered-out game is still ticked
        if (game.selected && game.present)
            total += gameTickedSize(game.stem);
    return total;
}

void GuiMadPageGranularGames::toggleGameAt(int i)
{
    if (i < 0 || i >= static_cast<int>(mShown.size()) || mRoot == nullptr)
        return;
    if (!mShown[i].present) {
        footer()->flash("ROM is missing on disk — can't include it.", 3000, true);
        return;
    }
    const bool on {!mShown[i].selected};
    const std::string stem {mShown[i].stem};
    mShown[i].selected = on;
    for (Game& gm : mGames)
        if (gm.stem == stem) {
            gm.selected = on;
            break;
        }
    // Only EXCLUSIONS are stored, so ticking a game back on simply forgets it (see mGameOff).
    if (on)
        mRoot->mGameOff[mSystem].erase(stem);
    else
        mRoot->mGameOff[mSystem].insert(stem);
    if (mList != nullptr && i < mList->size())
        mList->setRow(i, rowGlyph(mShown[i]) + rowText(mShown[i]), rowColor(mShown[i]));
    if (mHeader != nullptr)
        mHeader->setText(headerText());
    updateDetail();
}

void GuiMadPageGranularGames::selectAllOrNone()
{
    if (mRoot == nullptr)
        return;
    // If ANYTHING is ticked, clear the lot; only when nothing is ticked does this tick everything.
    bool any {false};
    for (const Game& game : mGames)
        if (game.selected)
            any = true;
    for (Game& game : mGames) {
        if (!game.present)
            continue;
        game.selected = !any;
        if (mSelectionSink != nullptr) {
            if (game.selected)
                mSelectionSink->insert(game.id);
            else
                mSelectionSink->erase(game.id);
        }
        else if (mBackup) {
            if (game.selected)
                mRoot->mGameOff[mSystem].erase(game.stem);
            else
                mRoot->mGameOff[mSystem].insert(game.stem);
        }
    }
    populate();
    footer()->flash(any ? "Nothing selected." : "Everything selected.", 2000, false);
}

void GuiMadPageGranularGames::backupTicked()
{
    if (mRoot == nullptr)
        return;
    if (mRoot->busy()) {
        footer()->flash("A backup or restore is already running — let it finish first.", 4000, true);
        return;
    }
    // Every ticked game, each with ITS OWN ticked asset keys - the shape both backup RPCs take.
    std::vector<GuiMadPageBackupRestore::AssetRestoreSel> items;
    for (const Game& game : mGames) {
        if (!game.selected || !game.present)
            continue;
        std::vector<std::string> keys;
        const auto it = mGameAssets.find(game.stem);
        if (it != mGameAssets.end()) {
            for (const AssetSize& a : it->second)
                if (mRoot->assetTicked(mSystem, game.stem, a.key))
                    keys.push_back(a.key);
            if (keys.empty())
                continue;   // every asset unticked = the user excluded this game asset by asset
        }
        // Sizing has not reached this game yet: send it with no keys, which the backend reads as
        // "everything this game has" - the default, and what the user has not overridden.
        items.push_back({mSystem, game.stem, keys});
    }
    if (items.empty()) {
        footer()->flash("Nothing is selected — press Y to include a game.", 3500, false);
        return;
    }
    mRoot->startGamesAssets(items, tickedTotal(), mSizesStreaming);
}

void GuiMadPageGranularGames::refreshSelectionSizes()
{
    if (!sizesApply() || mDetail == nullptr)
        return;
    if (mSelInFlight) { // one at a time; the in-flight reply re-fires for the newer selection
        mSelDirty = true;
        return;
    }
    // The selection is the master list's ticks (mShown is only a filtered view, and a filtered-out
    // game stays ticked), so a size total never changes just because the user typed in the search.
    std::vector<std::pair<std::string, std::string>> picked; // (system, stem)
    for (const Game& gm : mGames)
        if (gm.selected)
            picked.emplace_back(mSystem, gm.stem);

    if (picked.empty()) {
        mSelSizes.clear();
        mSelTotal = 0;
        mSelPartial = false;
        mSelSkipped = 0;
        updateDetail();
        return;
    }

    mSelInFlight = true;
    mSelDirty = false;
    const unsigned int gen {++mSelGen};
    if (mSelSizes.empty())
        mDetail->setText("Adding up…"); // first run only: never blank an already-shown total
    pageRequest(
        "granular.selection_sizes",
        [picked](MadJson::Writer& w) {
            w.Key("items");
            w.StartArray();
            for (const auto& it : picked) {
                w.StartObject();
                w.Key("system");
                w.String(it.first.c_str(), static_cast<rapidjson::SizeType>(it.first.length()));
                w.Key("stem");
                w.String(it.second.c_str(), static_cast<rapidjson::SizeType>(it.second.length()));
                w.EndObject();
            }
            w.EndArray();
            // Size EXACTLY what this cart's backup copies: granular.backup(category='roms') plans one
            // path per game, the ROM. Summing media/saves/states here would promise a backup several GB
            // bigger than the button performs (review 2026-07-31).
            w.Key("keys");
            w.StartArray();
            w.String("rom");
            w.EndArray();
        },
        [this, gen](bool ok, const rapidjson::Value& payload) {
            mSelInFlight = false;
            if (gen == mSelGen) { // a stale reply (selection moved on) is dropped whole
                if (ok) {
                    mSelSizes.clear();
                    const rapidjson::Value& arr {MadJson::getMember(payload, "games")};
                    if (arr.IsArray())
                        for (const rapidjson::Value& g : arr.GetArray())
                            mSelSizes.push_back({MadJson::getString(g, "name"),
                                                 MadJson::getInt64(g, "size", 0),
                                                 MadJson::getBool(g, "size_partial", false)});
                    mSelTotal = MadJson::getInt64(payload, "total", 0);
                    mSelPartial = MadJson::getBool(payload, "total_partial", false);
                    mSelSkipped = static_cast<int>(MadJson::getInt64(payload, "skipped", 0));
                    updateDetail();
                }
                else if (mDetail != nullptr && mSelSizes.empty()) {
                    // Say so, rather than leaving "Adding up…" on screen forever (a timeout or a
                    // backend error would otherwise look like a walk that never finishes).
                    mDetail->setText("Couldn't add up sizes");
                }
            }
            if (mSelDirty) // the user ticked something while this was in flight
                refreshSelectionSizes();
        },
        12000); // sizing walks the disk; the backend caps itself just under this
}

namespace
{
    // Cut to at most `maxChars` CHARACTERS, never mid-UTF-8-sequence: game names carry accents and CJK
    // ("Pokémon", "Berserk - Millennium Falcon…"), and a byte-wise substr would emit a broken sequence
    // that renders as garbage right before the ellipsis (review 2026-07-31).
    std::string ellipsize(const std::string& text, const size_t maxChars)
    {
        if (Utils::String::unicodeLength(text) <= maxChars)
            return text;
        size_t cursor {0};
        for (size_t i {0}; i < maxChars - 1 && cursor < text.length(); ++i)
            cursor = Utils::String::nextCursor(text, cursor);
        return text.substr(0, cursor) + "…";
    }
} // namespace

void GuiMadPageGranularGames::updateDetail()
{
    if (mDetail == nullptr)
        return;
    if (mBackup) {
        // BACKUP browse: the FOCUSED game broken down by asset, that game's ticked total, and the
        // whole system's ticked total underneath (user 2026-07-31, mocked).
        std::string text;
        const int c {mList != nullptr ? mList->cursor() : -1};
        if (c >= 0 && c < static_cast<int>(mShown.size())) {
            const Game& game {mShown[c]};
            const auto it = mGameAssets.find(game.stem);
            if (it == mGameAssets.end()) {
                text += mSizesStreaming ? "Sizing…\n" : "\n";
            }
            else if (it->second.empty()) {
                text += "Nothing to back up\n";
            }
            else {
                for (const AssetSize& a : it->second) {
                    std::string line {a.label + ":  " + MadPageUtil::humanSize(a.size)};
                    if (a.count > 1)
                        line += "  ·  " + std::to_string(a.count) + " Files";
                    if (mRoot != nullptr && !mRoot->assetTicked(mSystem, game.stem, a.key))
                        line += "   (off)";
                    text += line + "\n";
                }
                text += " Selected:  " + MadPageUtil::humanSize(gameTickedSize(game.stem)) + "\n";
            }
        }
        text += "\nTotal selected: " + MadPageUtil::humanSize(tickedTotal());
        // 51 s for the biggest system here, so SAY how far it has got rather than look frozen.
        if (mSizesStreaming && mSizeTotalN > 0)
            text += "\n(adding up " + std::to_string(mSizedN) + "/" + std::to_string(mSizeTotalN) + ")";
        mDetail->setText(text);
        return;
    }
    if (mSelSizes.empty()) {
        mDetail->setText("Nothing selected");
        return;
    }
    // The band is short, so show the biggest first and say how many were left out - the games that
    // dominate the total are the ones worth seeing.
    std::vector<const SelSize*> ordered;
    ordered.reserve(mSelSizes.size());
    for (const SelSize& s : mSelSizes)
        ordered.push_back(&s);
    std::stable_sort(ordered.begin(), ordered.end(),
                     [](const SelSize* a, const SelSize* b) { return a->size > b->size; });

    // How many lines actually FIT: TextComponent does not clip vertically, so a fixed row count
    // would spill past the viewport on a short band (review 2026-07-31). Reserve the tail lines the
    // summary always needs - blank + "N selected" (+ "still counting") - and give the rest to games.
    const float lineHeight {std::max(1.0f, Font::get(FONT_SIZE_SMALL)->getHeight())};
    const int linesFit {static_cast<int>(mDetail->getSize().y / lineHeight)};
    const int tailLines {2 + (mSelSkipped > 0 ? 1 : 0)};
    const int rowBudget {std::max(1, linesFit - tailLines)};
    const int total {static_cast<int>(ordered.size())};
    // Only spend a slot on "+N more" when it actually hides something.
    const int maxRows {total > rowBudget ? std::max(1, rowBudget - 1) : rowBudget};

    std::string text;
    int shown {0};
    for (const SelSize* s : ordered) {
        if (shown >= maxRows)
            break;
        text += ellipsize(s->name, 24) + "   " + MadPageUtil::humanSize(s->size) +
                (s->partial ? "+" : "") + "\n";
        ++shown;
    }
    const int more {total - shown};
    if (more > 0)
        text += "+ " + std::to_string(more) + " more\n";

    text += "\n" + std::to_string(total) + " selected:  " + MadPageUtil::humanSize(mSelTotal) +
            (mSelPartial ? "+" : "");
    if (mSelSkipped > 0) // be explicit that the total is a floor, and by how much
        text += "\n(" + std::to_string(mSelSkipped) + " still counting)";
    mDetail->setText(text);
}

void GuiMadPageGranularGames::populate()
{
    ensureWidgets();

    const std::string f {MadPageUtil::lower(mFilter)};
    mShown.clear();
    for (const Game& game : mGames)
        if (f.empty() || MadPageUtil::lower(rowText(game)).find(f) != std::string::npos ||
            MadPageUtil::lower(game.stem).find(f) != std::string::npos)
            mShown.push_back(game);

    mHeader->setText(headerText());

    std::vector<MadVirtualList::Row> rows;
    rows.reserve(mShown.size() + 1);
    // RESTORE: a synthetic TOP row restores THIS whole system at once (count is the full system's games,
    // independent of any active filter). BACKUP has no row (Square backs up all); SELECT has neither.
    if (hasTopAllRow()) {
        const std::string n {std::to_string(mGames.size())};
        rows.push_back({"> Restore all " + n + (mGames.size() == 1 ? " game" : " games"),
                        MadTheme::color(MadColor::Title)});
    }
    for (const Game& game : mShown)
        rows.push_back({rowGlyph(game) + rowText(game), rowColor(game)});
    mList->setRows(rows, /*keepCursor=*/false);

    mPanel->refreshHelpPrompts();
    updatePreview();
    if (mBackup)
        startSystemSizes();  // exact per-asset sizes for this system, streamed
    else
        refreshSelectionSizes();
    updateDetail();
}

void GuiMadPageGranularGames::updatePreview()
{
    if (mPreview == nullptr)
        return;
    const int c {mList != nullptr ? mList->cursor() : -1};
    const int gi {hasTopAllRow() ? c - 1 : c}; // account for the restore-only top "All" row at index 0
    if (gi >= 0 && gi < static_cast<int>(mShown.size()))
        mPreview->setImage(mShown[gi].art);
    else
        mPreview->setImage("");
}

void GuiMadPageGranularGames::activateAt(int i)
{
    if (hasTopAllRow()) {
        if (i == 0) { // the synthetic TOP "> Restore all" row
            allAction();
            return;
        }
        toggleAt(i - 1); // restore: A toggles the game's selection (rows shifted by the top all-row)
        return;
    }
    if (mBackup)
        openAssetsAt(i);  // game-first backup: A drills into this game's assets
    else
        toggleAt(i);      // select mode: A toggles this game's selection
}

void GuiMadPageGranularGames::allAction()
{
    if (mRoot == nullptr)
        return;
    if (mRoot->busy()) {
        footer()->flash("A backup or restore is already running; let it finish first.", 4000, true);
        return;
    }
    if (mBackup)
        mRoot->backupAll("system", mSystem);   // back up EVERY game in this system (ROM+saves+states+media)
    else
        mRoot->restoreAll(mSystem);             // restore every game this backup holds for this system
}

void GuiMadPageGranularGames::openAssetsAt(int i)
{
    if (i < 0 || i >= static_cast<int>(mShown.size()) || mRoot == nullptr)
        return;
    if (mRoot->busy()) {
        footer()->flash("A backup or restore is already running — let it finish first.", 4000, true);
        return;
    }
    const Game& game {mShown[i]};
    mRoot->openGameAssets(mSystem, game.stem, game.name, game.art);
}

void GuiMadPageGranularGames::toggleAt(int i)
{
    if (i < 0 || i >= static_cast<int>(mShown.size()))
        return;
    if (!mShown[i].present) {
        footer()->flash("ROM is missing on disk — can't include it.", 3000, true);
        return;
    }
    const bool on {!mShown[i].selected};
    const std::string id {mShown[i].id};
    mShown[i].selected = on;
    // write through to the master list so a re-filter keeps the selection (id is the unique key)
    for (Game& gm : mGames)
        if (gm.id == id) {
            gm.selected = on;
            break;
        }
    if (mSelectionSink != nullptr) { // SELECT mode: reflect into the cross-system cart
        if (on)
            mSelectionSink->insert(id);
        else
            mSelectionSink->erase(id);
    }
    // relabel the on-screen row: in restore mode the synthetic top "All" row shifts every game down by 1,
    // so the LIST index is i+1 (activateAt/updatePreview/input-Y apply the same offset).
    const int listIndex {hasTopAllRow() ? i + 1 : i};
    if (mList != nullptr && listIndex < mList->size())
        mList->setRow(listIndex, rowGlyph(mShown[i]) + rowText(mShown[i]), rowColor(mShown[i]));
    if (mHeader != nullptr)
        mHeader->setText(headerText());
    refreshSelectionSizes(); // the under-art list + total follow the tick
}

void GuiMadPageGranularGames::openSearch()
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

void GuiMadPageGranularGames::act()
{
    if (mSelectionSink != nullptr)
        return; // SELECT mode: the tick IS the output; there is no X action (B returns to the picker)
    if (mBackup) {
        allAction();  // BACKUP: Square (X) backs up ALL games in this system (A still drills one game)
        return;
    }
    if (mRoot == nullptr)
        return;
    if (mRoot->busy()) {
        footer()->flash("A backup or restore is already running — let it finish first.", 4000, true);
        return;
    }
    std::vector<const Game*> chosen;
    for (const Game& game : mGames)  // selection lives on the master list (persists across a filter)
        if (game.selected)
            chosen.push_back(&game);
    if (chosen.empty()) {
        footer()->flash("Select some games first (press A to tick them).", 3500, false);
        return;
    }
    // restore ALL of each ticked game's backed-up assets (empty keys = all) - local OR cloud, per-asset OR
    // whole-ROM (item_game unifies both). The durable root previews (WARN on a replace) then rule-5 restores.
    std::vector<GuiMadPageBackupRestore::AssetRestoreSel> games;
    for (const Game* g : chosen)
        games.push_back({mSystem, g->stem, {}});
    mRoot->restoreAssets(games);
}

bool GuiMadPageGranularGames::input(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("y", input) && mList != nullptr) {
        if (restoreMode())
            // drill into ONE game's per-asset restore picks; the top "All" row (cursor 0) has no drill,
            // and openAssetsAt bounds-checks the -1 it gets there.
            openAssetsAt(hasTopAllRow() ? mList->cursor() - 1 : mList->cursor());
        else if (mBackup)
            toggleGameAt(mList->cursor());   // exclude/include a whole game (A still OPENS it)
        else
            openSearch();                    // select mode: Y searches the (full) list
        return true;
    }
    // Search moves to L3 in the backup browse, because Y now ticks. R3 is all-or-none everywhere
    // there are ticks.
    if (input.value != 0 && mBackup && config->isMappedTo("leftthumbstickclick", input)) {
        openSearch();
        return true;
    }
    if (input.value != 0 && config->isMappedTo("rightthumbstickclick", input) &&
        (mBackup || mSelectionSink != nullptr)) {
        selectAllOrNone();
        return true;
    }
    if (input.value != 0 && config->isMappedTo("x", input)) {
        if (mBackup)
            backupTicked();                  // back up exactly what is ticked, not the whole system
        else
            act();
        return true;
    }
    return mList != nullptr ? mList->input(config, input) : false;
}

bool GuiMadPageGranularGames::consumesSectionNav()
{
    // Leaving during a job is allowed now (the durable root's op keeps running on the daemon), so the
    // shoulder section-switch no longer needs to be blocked here either.
    return false;
}

void GuiMadPageGranularGames::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

void GuiMadPageGranularGames::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->cursor();
}

void GuiMadPageGranularGames::onRestoreFocus()
{
    if (mList != nullptr)
        mList->setCursor(mFocusCookie);
}

std::vector<HelpPrompt> GuiMadPageGranularGames::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"),
                                     HelpPrompt("a", mBackup ? "open" : "select")};
    if (mBackup)
        prompts.push_back(HelpPrompt("x", "back up")); // Square backs up exactly what is ticked
    else if (mSelectionSink == nullptr) // restore: X restores the ticked games (A ticks; top row = all)
        prompts.push_back(HelpPrompt("x", "restore"));
    // In the backup browse Y ticks (A already opens the game), so search moves to L3 and the
    // all-or-none gesture sits on R3 next to it.
    prompts.push_back(HelpPrompt("y", restoreMode() ? "pick assets"
                                                    : (mBackup ? "include/exclude" : "search")));
    // One stick-click glyph covers both (the engine has a single "thumbstickclick" icon): L3
    // searches in the backup browse, R3 is all-or-none wherever there are ticks.
    if (mBackup)
        prompts.push_back(HelpPrompt("thumbstickclick", "search / all / none"));
    else if (mSelectionSink != nullptr)
        prompts.push_back(HelpPrompt("thumbstickclick", "all / none"));
    if (mList != nullptr && mList->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    prompts.push_back(HelpPrompt("b", mSelectionSink != nullptr ? "done" : "back"));
    return prompts;
}
