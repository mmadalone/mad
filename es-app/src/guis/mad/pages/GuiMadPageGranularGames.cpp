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

#include <functional>

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
    if (mBackup)
        return "";  // game-first drill: no selection glyph (A opens the game's assets)
    if (!game.present)
        return "⚠ ";
    return game.selected ? "● " : "○ ";
}

unsigned int GuiMadPageGranularGames::rowColor(const Game& game) const
{
    if (mBackup)
        return MadTheme::color(MadColor::Primary);    // every game is drillable (may have saves/media)
    if (!game.present)
        return MadTheme::color(MadColor::Red);        // "ROM missing" — dimmed, not selectable
    return game.selected ? MadTheme::color(MadColor::Primary)
                         : MadTheme::color(MadColor::Secondary);
}

std::string GuiMadPageGranularGames::headerText() const
{
    const std::string count {std::to_string(mShown.size()) +
                             (mFilter.empty() ? " games · " : " matches · ")};
    if (mBackup)  // game-first: no selection; A opens the game's assets
        return count + "Y search · A open";
    int selected {0};
    for (const Game& game : mGames)  // count this system's ticked games (the sink is cross-system)
        if (game.selected)
            ++selected;
    const std::string tail {mSelectionSink != nullptr ? "Y search · B when done"
                                                      : "Y search · X restore"};
    return count + std::to_string(selected) + " selected · " + tail;
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
                    game.selected = mSelectionSink != nullptr && mSelectionSink->count(game.id) > 0;
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
    mList->setOnCursorChanged([this](int) { updatePreview(); });
    addChild(mList.get());
    mList->onFocusGained();

    mPreview = MadPageUtil::makeBezelPreview(mViewportPos, mViewportSize, listWidth);
    addChild(mPreview.get());
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
    rows.reserve(mShown.size());
    for (const Game& game : mShown)
        rows.push_back({rowGlyph(game) + rowText(game), rowColor(game)});
    mList->setRows(rows, /*keepCursor=*/false);

    mPanel->refreshHelpPrompts();
    updatePreview();
}

void GuiMadPageGranularGames::updatePreview()
{
    if (mPreview == nullptr)
        return;
    const int c {mList != nullptr ? mList->cursor() : -1};
    if (c >= 0 && c < static_cast<int>(mShown.size()))
        mPreview->setImage(mShown[c].art);
    else
        mPreview->setImage("");
}

void GuiMadPageGranularGames::activateAt(int i)
{
    if (mBackup)
        openAssetsAt(i);  // game-first: A drills into this game's assets
    else
        toggleAt(i);      // select/restore: A toggles this game's selection
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
    if (mList != nullptr && i < mList->size())
        mList->setRow(i, rowGlyph(mShown[i]) + rowText(mShown[i]), rowColor(mShown[i]));
    if (mHeader != nullptr)
        mHeader->setText(headerText());
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
    if (mBackup)
        return; // game-first BACKUP: A drills into the game's asset list; there is no X action here
    if (mRoot == nullptr)
        return;
    if (mRoot->busy()) {
        footer()->flash("A backup or restore is already running — let it finish first.", 4000, true);
        return;
    }
    if (mActing)
        return; // a restore preview is already in flight (ignore a rapid second press)
    std::vector<std::pair<std::string, std::string>> items; // (system, id) restore
    for (const Game& game : mGames)
        if (game.selected)
            items.emplace_back(mSystem, game.id);
    if (items.empty()) {
        footer()->flash("Select some games first (press A to tick them).", 3500, false);
        return;
    }
    doRestore(items, /*warned=*/false);
}

void GuiMadPageGranularGames::doRestore(const std::vector<std::pair<std::string, std::string>>& items,
                                        bool warned)
{
    // Ask the backend which selected games already exist live (a REPLACE) so we can warn before writing.
    const std::string source {mSource};
    const std::string category {mCategory};
    std::weak_ptr<int> alive {pageAlive()};
    mActing = true; // in flight until the preview responds (guards a double X-press stacking two dialogs)
    pageRequest(
        "granular.restore_preview",
        [source, category, items](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
            w.Key("category");
            w.String(category.c_str(), static_cast<rapidjson::SizeType>(category.length()));
            w.Key("items");
            w.StartArray();
            for (const auto& it : items) {
                w.StartObject();
                w.Key("system");
                w.String(it.first.c_str(), static_cast<rapidjson::SizeType>(it.first.length()));
                w.Key("id");
                w.String(it.second.c_str(), static_cast<rapidjson::SizeType>(it.second.length()));
                w.EndObject();
            }
            w.EndArray();
        },
        [this, alive, items, source, category](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            mActing = false; // preview responded; the modal confirm (if any) now serializes input
            if (!ok) {
                footer()->flash("Couldn't check the backup: " +
                                    MadJson::getString(payload, "message", "error"),
                                5000, true);
                return;
            }
            int replace {0};
            const rapidjson::Value& arr {MadJson::getMember(payload, "replace")};
            if (arr.IsArray())
                replace = static_cast<int>(arr.Size());
            auto start = [this, items, source, category] {
                mRoot->startRestore(category, source, items);
                footer()->setStatus("Restoring " + std::to_string(items.size()) + " game(s)…");
            };
            if (replace > 0) {
                std::weak_ptr<int> a2 {pageAlive()};
                mWindow->pushGui(new MadMsgBox(
                    std::to_string(replace) + " of these game(s) are already on disk and will be "
                    "REPLACED. A recoverable copy is saved first. Continue?",
                    "YES", [a2, start] { if (!a2.expired()) start(); }, "CANCEL", nullptr));
            }
            else {
                start();
            }
        },
        15000);
}

bool GuiMadPageGranularGames::input(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("y", input) && mList != nullptr) {
        openSearch();
        return true;
    }
    if (input.value != 0 && config->isMappedTo("x", input)) {
        act();
        return true;
    }
    return mList != nullptr ? mList->input(config, input) : false;
}

bool GuiMadPageGranularGames::consumesSectionNav()
{
    return mRoot != nullptr && mRoot->busy();
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
    if (mSelectionSink == nullptr && !mBackup) // only restore has an X action (open/tick are A)
        prompts.push_back(HelpPrompt("x", "restore"));
    prompts.push_back(HelpPrompt("y", "search"));
    if (mList != nullptr && mList->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    prompts.push_back(HelpPrompt("b", mSelectionSink != nullptr ? "done" : "back"));
    return prompts;
}
