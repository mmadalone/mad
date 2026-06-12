//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageGamepads.cpp
//
//  MAD control panel: Gamepad tester (deck-patches).
//

#include "guis/mad/pages/GuiMadPageGamepads.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"

#include <cmath>

//  ── GuiMadPageGamepads (picker) ──

GuiMadPageGamepads::GuiMadPageGamepads(GuiMadPanel* panel)
    : MadPage {panel, "GAMEPAD TESTER"}
{
}

void GuiMadPageGamepads::build()
{
    mIntro = std::make_shared<TextComponent>(
        "Pick a connected controller, then press its controls and watch them light up. "
        "Real Wii Remotes on a DolphinBar (mode 4) show up per live slot; the X-Arcade "
        "has its own page. Wake sleeping BT pads (press a button) and they appear here "
        "automatically.",
        Font::get(FONT_SIZE_SMALL), mMenuColorPrimary, ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(mViewportPos.x, mViewportPos.y);
    mIntro->setSize(mViewportSize.x, 0.0f);
    addChild(mIntro.get());

    mPanel->ensureDeviceWatch(); // Instant refresh on evdev pad hotplug.
    mPollAccum = 0;
    refreshList();
}

std::string GuiMadPageGamepads::padsSignature(const rapidjson::Value& payload)
{
    std::string signature;
    const rapidjson::Value& pads {MadJson::getMember(payload, "pads")};
    if (pads.IsArray()) {
        for (rapidjson::SizeType i {0}; i < pads.Size(); ++i) {
            const rapidjson::Value& p {pads[i]};
            signature.append(MadJson::getString(p, "kind"))
                .append("|")
                .append(MadJson::getString(p, "path"))
                .append("|")
                .append(MadJson::getString(p, "node"))
                .append("|")
                .append(std::to_string(MadJson::getInt(p, "slot")))
                .append("|")
                .append(MadJson::getString(p, "ext"))
                .append("|")
                .append(MadJson::getString(p, "name"))
                .append("|")
                .append(MadJson::getString(p, "uniq"))
                .append("\n");
        }
    }
    return signature;
}

void GuiMadPageGamepads::refreshList()
{
    mScanInFlight = true;
    setLoadingText("Scanning controllers (and DolphinBar slots)…");
    pageRequest(
        "gamepads.list", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            mScanInFlight = false;
            if (!ok) {
                setLoadingText("");
                footer()->setStatus("Couldn't scan: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            applyList(payload);
        },
        15000);
}

void GuiMadPageGamepads::silentRefresh()
{
    if (mScanInFlight)
        return;
    mScanInFlight = true;
    pageRequest(
        "gamepads.list", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            mScanInFlight = false;
            if (!ok)
                return; // Background poll — stay quiet, retry next tick.
            if (padsSignature(payload) == mListSignature)
                return; // Same pads — don't churn the grid (or the cursor).
            applyList(payload);
        },
        15000);
}

void GuiMadPageGamepads::applyList(const rapidjson::Value& payload)
{
    const int cursor {mGrid != nullptr ? mGrid->cursorIndex() : mFocusCookie};
    if (mGrid != nullptr) {
        removeChild(mGrid.get());
        mGrid.reset();
    }
    setLoadingText("");
    mListSignature = padsSignature(payload);
    mPads.clear();
    std::vector<MadTileGrid::Tile> tiles;
    const rapidjson::Value& pads {MadJson::getMember(payload, "pads")};
    if (pads.IsArray()) {
        for (rapidjson::SizeType i {0}; i < pads.Size(); ++i) {
            const rapidjson::Value& p {pads[i]};
            const rapidjson::Value& prof {MadJson::getMember(p, "profile")};
            Pad pad;
            pad.kind = MadJson::getString(p, "kind");
            pad.path = MadJson::getString(p, "path");
            pad.node = MadJson::getString(p, "node");
            pad.slot = MadJson::getInt(p, "slot");
            pad.ext = MadJson::getString(p, "ext");
            pad.name = MadJson::getString(p, "name");
            pad.idtail = MadJson::getString(p, "idtail");
            pad.uniq = MadJson::getString(p, "uniq");
            pad.profileKey = MadJson::getString(prof, "key");
            pad.profileLabel = MadJson::getString(prof, "label");
            pad.profileDir = MadJson::getString(prof, "dir");
            pad.iconPath = MadJson::getString(prof, "icon_path");
            mPads.emplace_back(pad);

            MadTileGrid::Tile tile;
            tile.key = std::to_string(i);
            // The PROFILE label ("DualShock 4"), not the raw evdev
            // name ("Wireless Controller").
            tile.label = pad.kind == "wii" ? pad.name : pad.profileLabel;
            tile.sublabel = pad.idtail;
            tile.artPath = pad.iconPath;
            tiles.emplace_back(tile);
        }
    }
    if (tiles.empty()) {
        setLoadingText("No supported controllers detected — wake a pad (press a "
                       "button; Wii Remotes need a 1+2 re-sync) and it appears here "
                       "automatically.");
        mPanel->refreshHelpPrompts();
        return;
    }
    const float top {mIntro->getPosition().y + mIntro->getSize().y +
                     Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f};
    mGrid = std::make_shared<MadTileGrid>();
    mGrid->setPosition(mViewportPos.x, top);
    mGrid->setSize(mViewportSize.x, mViewportPos.y + mViewportSize.y - top);
    mGrid->setTiles(tiles);
    mGrid->setOnPick([this](const std::string& key) {
        const size_t index {static_cast<size_t>(std::stoul(key))};
        if (index >= mPads.size())
            return;
        const Pad& pad {mPads[index]};
        mPanel->pushPage(new GuiMadPageGamepadTest(
            mPanel, pad.kind, pad.path, pad.node, pad.slot, pad.ext, pad.name,
            pad.idtail, pad.uniq, pad.profileKey, pad.profileLabel,
            pad.profileDir));
    });
    mGrid->setCursorIndex(cursor);
    mGrid->onFocusGained(); // Only focusable here.
    addChild(mGrid.get());
    mPanel->refreshHelpPrompts();
}

void GuiMadPageGamepads::update(int deltaTime)
{
    // Wiimote sync/sleep changes no /dev/input node, so devices.watch never
    // fires for it — poll the (slow, worker-pool) scan while this page is on
    // top. The probe is skipped during captures; a running test means the test
    // page is on top, so this update doesn't run at all.
    mPollAccum += deltaTime;
    if (mPollAccum >= 4000) {
        mPollAccum = 0;
        if (!mPanel->isInputLocked())
            silentRefresh();
    }
    GuiComponent::update(deltaTime);
}

bool GuiMadPageGamepads::input(InputConfig* config, Input input)
{
    if (mGrid != nullptr)
        return mGrid->input(config, input);
    return false;
}

void GuiMadPageGamepads::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageGamepads::getHelpPrompts()
{
    if (mGrid != nullptr)
        return mGrid->getHelpPrompts();
    return std::vector<HelpPrompt>();
}

void GuiMadPageGamepads::onSaveFocus()
{
    if (mGrid != nullptr)
        mFocusCookie = mGrid->cursorIndex();
}

void GuiMadPageGamepads::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mFocusCookie);
}

//  ── GuiMadPageGamepadTest ──

GuiMadPageGamepadTest::GuiMadPageGamepadTest(
    GuiMadPanel* panel, const std::string& kind, const std::string& path,
    const std::string& node, const int slot, const std::string& ext,
    const std::string& name, const std::string& idtail, const std::string& uniq,
    const std::string& profileKey, const std::string& profileLabel,
    const std::string& profileDir)
    : MadLightgunPageBase {panel, profileLabel + " TESTER"}
    , mKind {kind}
    , mPath {path}
    , mNode {node}
    , mExt {ext}
    , mName {name}
    , mIdtail {idtail}
    , mUniq {uniq}
    , mProfileKey {profileKey}
    , mProfileLabel {profileLabel}
    , mProfileDir {profileDir}
    , mSlot {slot}
    , mRunning {false}
    , mEditMode {false}
    , mCalMode {false}
    , mP2 {false}
    , mNudgeDx {0}
    , mNudgeDy {0}
    , mNudgeAccum {0}
{
}

GuiMadPageGamepadTest::~GuiMadPageGamepadTest()
{
    // Any way the page dies, release the grab (the daemon also auto-releases
    // on idle/escape/teardown — this covers in-panel navigation).
    if (!mStreamToken.empty())
        backend()->clearStreamCallback(mStreamToken);
    if (mRunning)
        backend()->request("tester.stop", nullptr, nullptr);
}

void GuiMadPageGamepadTest::build()
{
    setLoadingText("Loading sprites…");
    const std::string key {mProfileKey};
    const std::string dir {mProfileDir};
    const std::string ext {mExt};
    const std::string uniq {mUniq};
    const std::string name {mName};
    pageRequest(
        "gamepads.layout",
        [key, dir, ext, uniq, name](MadJson::Writer& writer) {
            writer.Key("key");
            writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            writer.Key("dir");
            writer.String(dir.c_str(), static_cast<rapidjson::SizeType>(dir.length()));
            if (!ext.empty()) {
                writer.Key("ext");
                writer.String(ext.c_str(), static_cast<rapidjson::SizeType>(ext.length()));
            }
            if (!uniq.empty()) {
                writer.Key("uniq");
                writer.String(uniq.c_str(),
                              static_cast<rapidjson::SizeType>(uniq.length()));
                writer.Key("name");
                writer.String(name.c_str(),
                              static_cast<rapidjson::SizeType>(name.length()));
            }
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load the sprites: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            rebuild(payload);
        },
        10000);
}

void GuiMadPageGamepadTest::buildCanvasItems(MadSpriteCanvas* canvas,
                                             const rapidjson::Value& sprites,
                                             const rapidjson::Value& positions,
                                             const std::vector<std::string>& allowed,
                                             const bool p2)
{
    if (!sprites.IsObject())
        return;
    // Stick token set: lstick_<token>.png stems.
    std::map<std::string, std::string> stickImages;
    std::vector<std::pair<std::string, std::string>> buttons;
    for (auto it = sprites.MemberBegin(); it != sprites.MemberEnd(); ++it) {
        const std::string stem {it->name.GetString()};
        const std::string path {it->value.IsString() ? it->value.GetString() : ""};
        if (path.empty() || stem == "base" || stem == "back")
            continue;
        if (stem.rfind("lstick_", 0) == 0) {
            stickImages[stem.substr(7)] = path;
            continue;
        }
        if (!allowed.empty() &&
            std::find(allowed.begin(), allowed.end(), stem) == allowed.end())
            continue;
        if (stem == "p2indicator" && !p2)
            continue;
        buttons.emplace_back(stem, path);
    }
    auto posOf = [&positions](const std::string& key, float& nx, float& ny) {
        const rapidjson::Value& entry {MadJson::getMember(positions, key.c_str())};
        if (entry.IsArray() && entry.Size() == 2 && entry[0].IsNumber() &&
            entry[1].IsNumber()) {
            nx = static_cast<float>(entry[0].GetDouble());
            ny = static_cast<float>(entry[1].GetDouble());
            return true;
        }
        return false;
    };
    int index {0};
    for (const auto& button : buttons) {
        float nx {0.08f + 0.12f * (index % 8)};
        float ny {0.15f + 0.25f * (index / 8)};
        posOf(button.first, nx, ny);
        canvas->addItem(button.first, nx, ny, {{"on", button.second}},
                        button.first == "p2indicator");
        ++index;
    }
    if (!stickImages.empty()) {
        float nx {0.3f}, ny {0.6f};
        posOf("lstick", nx, ny);
        canvas->addItem("lstick", nx, ny, stickImages, true, "rest");
        // Wiimote/nunchuk panels have one stick; pads with rsticks save both.
        if (allowed.empty() || std::find(allowed.begin(), allowed.end(),
                                         "rstick") != allowed.end()) {
            float rx {0.7f}, ry {0.6f};
            if (posOf("rstick", rx, ry) || allowed.empty())
                canvas->addItem("rstick", rx, ry, stickImages, true, "rest");
        }
    }
}

void GuiMadPageGamepadTest::rebuild(const rapidjson::Value& layout)
{
    mP2 = MadJson::getBool(layout, "p2");
    mStems.clear();
    const rapidjson::Value& sprites {MadJson::getMember(layout, "sprites")};
    if (sprites.IsObject()) {
        for (auto it = sprites.MemberBegin(); it != sprites.MemberEnd(); ++it)
            mStems.emplace_back(it->name.GetString());
    }

    beginColumn();
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    addBlock(mName + "   ·   " + mIdtail, FONT_SIZE_MINI, mMenuColorSecondary,
             smallHeight * 0.3f);

    const std::string basePath {MadJson::getString(sprites, "base")};
    if (basePath.empty()) {
        addBlock("(sprites not found — expected icons/" + mProfileDir + "/base.png)",
                 FONT_SIZE_SMALL, mMenuColorSecondary, 0.0f);
        endColumn();
        return;
    }

    // Buttons FIRST (their true wrapped height decides the canvas room), then
    // pushed down so they sit just above the footer and the art fills the rest.
    const float contentTop {mY};
    const size_t controlsBefore {mControls.size()};
    std::vector<std::pair<std::string, std::function<void()>>> bar;
    bar.emplace_back("START TEST", [this] { startTest(); });
    bar.emplace_back("STOP", [this] { stopTest(); });
    if (mKind != "wii")
        bar.emplace_back("CALIBRATE", [this] { toggleCalibrate(); });
    bar.emplace_back("EDIT POSITIONS", [this] { toggleEdit(); });
    bar.emplace_back("SAVE LAYOUT", [this] { savePositions(); });
    if (std::find(mStems.begin(), mStems.end(), "p2indicator") != mStems.end() &&
        !mUniq.empty())
        bar.emplace_back(mP2 ? "P2 ✓" : "MARK P2", [this] { toggleP2(); });
    addButtonRow(bar);
    const float rowHeight {mY - contentTop};
    const float gapY {smallHeight * 0.4f};
    const float targetBottom {mViewportSize.y - smallHeight * 0.5f};
    const float availHeight {std::max(mViewportSize.y * 0.25f,
                                      targetBottom - contentTop - rowHeight - gapY)};
    moveControls(controlsBefore, availHeight + gapY);
    mY = contentTop + availHeight + gapY + rowHeight;

    // The canvases (core + optional accessory) — not focus controls; sized so
    // the art fills the area between the header and the button row. Core and
    // accessory share ONE scale and sit side by side with a tight gap.
    mCanvas = std::make_shared<MadSpriteCanvas>();
    mCanvas->setBase(basePath, MadJson::getString(sprites, "back"));

    mExtCanvas.reset();
    mExtKind.clear();
    std::vector<std::string> extAllowed;
    const rapidjson::Value& ext {MadJson::getMember(layout, "ext")};
    if (ext.IsObject()) {
        mExtKind = MadJson::getString(ext, "kind");
        const rapidjson::Value& extSprites {MadJson::getMember(ext, "sprites")};
        const std::string extBase {MadJson::getString(extSprites, "base")};
        if (!extBase.empty()) {
            mExtCanvas = std::make_shared<MadSpriteCanvas>();
            mExtCanvas->setBase(extBase);
            const rapidjson::Value& allowedArr {MadJson::getMember(ext, "allowed")};
            if (allowedArr.IsArray()) {
                for (rapidjson::SizeType i {0}; i < allowedArr.Size(); ++i)
                    extAllowed.emplace_back(allowedArr[i].GetString());
            }
            extAllowed.emplace_back("rstick");
        }
    }

    const glm::vec2 coreNative {mCanvas->nativeSize()};
    if (mExtCanvas != nullptr) {
        const glm::vec2 extNative {mExtCanvas->nativeSize()};
        const float gapX {mViewportSize.x * 0.03f};
        const float scale {std::min(availHeight / std::max(coreNative.y, extNative.y),
                                    (mViewportSize.x - gapX) /
                                        (coreNative.x + extNative.x))};
        const glm::vec2 coreBox {coreNative * scale};
        const glm::vec2 extBox {extNative * scale};
        const float x0 {(mViewportSize.x - (coreBox.x + gapX + extBox.x)) / 2.0f};
        mCanvas->setPosition(x0, contentTop + (availHeight - coreBox.y) / 2.0f);
        mCanvas->setSize(coreBox.x, coreBox.y);
        mExtCanvas->setPosition(x0 + coreBox.x + gapX,
                                contentTop + (availHeight - extBox.y) / 2.0f);
        mExtCanvas->setSize(extBox.x, extBox.y);
    }
    else {
        const float scale {
            std::min(availHeight / coreNative.y, mViewportSize.x / coreNative.x)};
        const glm::vec2 coreBox {coreNative * scale};
        mCanvas->setPosition((mViewportSize.x - coreBox.x) / 2.0f,
                             contentTop + (availHeight - coreBox.y) / 2.0f);
        mCanvas->setSize(coreBox.x, coreBox.y);
    }

    buildCanvasItems(mCanvas.get(), sprites, MadJson::getMember(layout, "positions"), {},
                     mP2);
    mScroll->addChild(mCanvas.get());
    mWidgets.emplace_back(mCanvas);
    if (mExtCanvas != nullptr) {
        buildCanvasItems(mExtCanvas.get(), MadJson::getMember(ext, "sprites"),
                         MadJson::getMember(ext, "positions"), extAllowed, false);
        mScroll->addChild(mExtCanvas.get());
        mWidgets.emplace_back(mExtCanvas);
    }
    endColumn();

    if (mProfileKey == "steamdeck")
        footer()->flash(
            "Heads-up: testing the Deck pad grabs it — you can't navigate while testing. "
            "Hold Start (6 s) or it auto-stops after ~20 s idle.",
            10000);
    else if (mKind == "wii")
        footer()->flash(
            "Real Wii Remote via the DolphinBar. START, then press its buttons — a "
            "Nunchuk/Classic lights up beside it. Hold + (6 s) to end.",
            10000);
}

void GuiMadPageGamepadTest::startTest()
{
    if (mRunning)
        return;
    const std::string kind {mKind == "wii" ? "wii" : "pad"};
    const std::string path {mPath};
    const std::string key {mProfileKey};
    const std::string node {mNode};
    const int slot {mSlot};
    const std::vector<std::string> stems {mStems};
    pageRequest(
        "tester.start",
        [kind, path, key, node, slot, stems](MadJson::Writer& writer) {
            writer.Key("kind");
            writer.String(kind.c_str(), static_cast<rapidjson::SizeType>(kind.length()));
            if (kind == "wii") {
                writer.Key("slot");
                writer.Int(slot);
                writer.Key("node");
                writer.String(node.c_str(),
                              static_cast<rapidjson::SizeType>(node.length()));
            }
            else {
                writer.Key("path");
                writer.String(path.c_str(),
                              static_cast<rapidjson::SizeType>(path.length()));
                writer.Key("key");
                writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
                writer.Key("stems");
                writer.StartArray();
                for (const std::string& stem : stems)
                    writer.String(stem.c_str(),
                                  static_cast<rapidjson::SizeType>(stem.length()));
                writer.EndArray();
            }
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't start: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            mRunning = true;
            const std::string token {MadJson::getString(payload, "stream")};
            if (!mStreamToken.empty())
                backend()->clearStreamCallback(mStreamToken);
            mStreamToken = token;
            std::weak_ptr<int> alive {pageAlive()};
            backend()->setStreamCallback(token,
                                         [this, alive](const rapidjson::Value& data) {
                                             if (alive.expired())
                                                 return;
                                             onStreamPush(data);
                                         });
            footer()->setStatus("Testing — press the controls. STOP or hold Start (6 s) "
                                "to end.");
        },
        10000);
}

void GuiMadPageGamepadTest::stopTest()
{
    pageRequest("tester.stop", nullptr, nullptr);
}

void GuiMadPageGamepadTest::onStreamPush(const rapidjson::Value& data)
{
    if (MadJson::getBool(data, "closed")) {
        mRunning = false;
        mPressed.clear();
        mStickState.clear();
        if (mCanvas != nullptr)
            mCanvas->resetItems();
        if (mExtCanvas != nullptr)
            mExtCanvas->resetItems();
        mWiiCore.clear();
        mWiiExt.clear();
        return;
    }
    const std::string ended {MadJson::getString(data, "ended")};
    if (!ended.empty()) {
        // Clear the sticky FIRST or the flash would restore the stale
        // "Testing…"/countdown text when it expires.
        footer()->setStatus("");
        footer()->flash(MadJson::getString(data, "message", "Stopped."), 4000);
        return; // The closed push follows and resets.
    }
    const std::string status {MadJson::getString(data, "status")};
    if (!status.empty() && mKind == "wii") {
        static const std::map<std::string, std::pair<std::string, bool>> messages {
            {"opening", {"Waking the Wii Remote…", false}},
            {"empty",
             {"That slot is empty now — press 1+2 on the remote, then re-enter.", true}},
            {"asleep", {"Wii Remote asleep — press 1+2 to re-sync. (Reconnecting…)", true}},
            {"live",
             {"Testing — press the Wii Remote (and Nunchuk/Classic if attached). STOP or "
              "hold + (6 s) to end.",
              false}},
            {"error", {"Couldn't read that slot — re-enter and retry.", true}}};
        const auto it = messages.find(status);
        if (it != messages.end())
            footer()->setStatus(it->second.first, it->second.second);
    }
    const bool counting {data.HasMember("countdown")};
    if (counting)
        footer()->setStatus("Keep holding to end the test…  " +
                            std::to_string(MadJson::getInt(data, "countdown")));
    const rapidjson::Value& spots {MadJson::getMember(data, "spots")};
    if (spots.IsObject() && mCanvas != nullptr) {
        for (auto it = spots.MemberBegin(); it != spots.MemberEnd(); ++it) {
            const bool on {it->value.IsBool() && it->value.GetBool()};
            mCanvas->setItemVisible(it->name.GetString(), on);
            mPressed[it->name.GetString()] = on;
        }
    }
    const rapidjson::Value& sticks {MadJson::getMember(data, "sticks")};
    if (sticks.IsObject() && mCanvas != nullptr) {
        for (auto it = sticks.MemberBegin(); it != sticks.MemberEnd(); ++it) {
            if (it->value.IsString()) {
                mCanvas->setItemToken(it->name.GetString(), it->value.GetString());
                mStickState[it->name.GetString()] = it->value.GetString();
            }
        }
    }
    const rapidjson::Value& wii {MadJson::getMember(data, "wii")};
    if (wii.IsObject())
        applyWii(wii);
    if (!counting && (spots.IsObject() || sticks.IsObject() || wii.IsObject()))
        refreshLiveFooter();
    const rapidjson::Value& bound {MadJson::getMember(data, "bound")};
    if (bound.IsObject())
        footer()->setStatus("✓ bound → \"" + MadJson::getString(bound, "spot") +
                            "\". Pick the next with the d-pad + A, or CALIBRATE to save.");
}

void GuiMadPageGamepadTest::refreshLiveFooter()
{
    // Live press reporting — the Tk testers' readout line, in the footer.
    std::string live;
    for (const auto& entry : mPressed) {
        if (entry.second)
            live += std::string(live.empty() ? "" : "   ·   ") + entry.first;
    }
    for (const std::string& stem : mWiiCore)
        live += std::string(live.empty() ? "" : "   ·   ") + stem;
    for (const std::string& stem : mWiiExt)
        live += std::string(live.empty() ? "" : "   ·   ") + stem;
    for (const auto& stick : mStickState) {
        if (stick.second != "rest" && !stick.second.empty())
            live += std::string(live.empty() ? "" : "   ·   ") + stick.first + " " +
                    stick.second;
    }
    footer()->setStatus(live.empty() ?
                            (mKind == "wii" ?
                                 "Testing — press the Wii Remote. Hold + (6 s) to end." :
                                 "Testing — press the controls. Hold Start (6 s) to end.") :
                            live);
}

void GuiMadPageGamepadTest::applyWii(const rapidjson::Value& wii)
{
    const std::string kind {MadJson::getString(wii, "kind")};
    if (kind != mExtKind)
        requestExtCanvas(kind); // Accessory plugged/unplugged mid-test.
    auto toSet = [&wii](const char* key) {
        std::set<std::string> out;
        const rapidjson::Value& arr {MadJson::getMember(wii, key)};
        if (arr.IsArray()) {
            for (rapidjson::SizeType i {0}; i < arr.Size(); ++i) {
                if (arr[i].IsString())
                    out.insert(arr[i].GetString());
            }
        }
        return out;
    };
    const std::set<std::string> core {toSet("core")};
    if (mCanvas != nullptr) {
        for (const std::string& stem : core)
            if (!mWiiCore.count(stem))
                mCanvas->setItemVisible(stem, true);
        for (const std::string& stem : mWiiCore)
            if (!core.count(stem))
                mCanvas->setItemVisible(stem, false);
    }
    mWiiCore = core;
    const std::set<std::string> ext {toSet("ext")};
    if (mExtCanvas != nullptr) {
        for (const std::string& stem : ext)
            if (!mWiiExt.count(stem))
                mExtCanvas->setItemVisible(stem, true);
        for (const std::string& stem : mWiiExt)
            if (!ext.count(stem))
                mExtCanvas->setItemVisible(stem, false);
        mExtCanvas->setItemToken("lstick", MadJson::getString(wii, "lstick", "rest"));
        mExtCanvas->setItemToken("rstick", MadJson::getString(wii, "rstick", "rest"));
    }
    mWiiExt = ext;
}

void GuiMadPageGamepadTest::requestExtCanvas(const std::string& kind)
{
    mExt = kind;
    mExtKind = kind;
    build(); // Full relayout with (or without) the accessory panel.
}

void GuiMadPageGamepadTest::toggleEdit()
{
    mEditMode = !mEditMode;
    if (mEditMode && mCalMode)
        toggleCalibrate();
    if (mCanvas != nullptr) {
        mCanvas->setAllVisible(mEditMode);
        mCanvas->setSelectionVisible(mEditMode);
    }
    if (mExtCanvas != nullptr)
        mExtCanvas->setAllVisible(mEditMode);
    if (mEditMode) {
        footer()->setStatus("Edit — A picks the next sprite, d-pad nudges it onto its "
                            "control, B exits. Then SAVE LAYOUT.");
    }
    else {
        footer()->setStatus("");
        footer()->flash("Edit off.");
    }
    mNudgeDx = mNudgeDy = 0;
}

void GuiMadPageGamepadTest::toggleCalibrate()
{
    if (mCalMode) {
        mCalMode = false;
        if (mCanvas != nullptr) {
            mCanvas->setSelectionVisible(false);
            mCanvas->setAllVisible(false);
        }
        pageRequest("tester.calibrate",
                    [](MadJson::Writer& writer) {
                        writer.Key("action");
                        writer.String("save");
                    },
                    [this](bool ok, const rapidjson::Value& payload) {
                        footer()->setStatus("");
                        footer()->flash(
                            MadJson::getString(payload, "message", "unknown error"), 4000,
                            !ok);
                    });
        stopTest();
        return;
    }
    if (mEditMode)
        toggleEdit();
    mCalMode = true;
    if (!mRunning)
        startTest();
    if (mCanvas != nullptr) {
        mCanvas->setAllVisible(true);
        mCanvas->setSelectionVisible(true);
    }
    footer()->setStatus("Calibrate — d-pad picks a sprite, A arms it, then press that "
                        "control on the pad. CALIBRATE again to save.");
}

void GuiMadPageGamepadTest::savePositions()
{
    if (mCanvas == nullptr)
        return;
    auto positions = mCanvas->positions();
    const std::string key {mProfileKey};
    pageRequest(
        "gamepads.positions_save",
        [key, positions](MadJson::Writer& writer) {
            writer.Key("key");
            writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            writer.Key("positions");
            writer.StartObject();
            for (const auto& entry : positions) {
                writer.Key(entry.first.c_str(),
                           static_cast<rapidjson::SizeType>(entry.first.length()));
                writer.StartArray();
                writer.Double(entry.second.first);
                writer.Double(entry.second.second);
                writer.EndArray();
            }
            writer.EndObject();
        },
        [this](bool ok, const rapidjson::Value& payload) {
            footer()->setStatus("");
            footer()->flash(MadJson::getString(payload, "message", "unknown error"), 4000,
                            !ok);
        });
    if (mExtCanvas != nullptr && !mExtKind.empty()) {
        auto extPositions = mExtCanvas->positions();
        const std::string extKey {mExtKind};
        pageRequest(
            "gamepads.positions_save",
            [extKey, extPositions](MadJson::Writer& writer) {
                writer.Key("key");
                writer.String(extKey.c_str(),
                              static_cast<rapidjson::SizeType>(extKey.length()));
                writer.Key("positions");
                writer.StartObject();
                for (const auto& entry : extPositions) {
                    writer.Key(entry.first.c_str(),
                               static_cast<rapidjson::SizeType>(entry.first.length()));
                    writer.StartArray();
                    writer.Double(entry.second.first);
                    writer.Double(entry.second.second);
                    writer.EndArray();
                }
                writer.EndObject();
            },
            nullptr);
    }
}

void GuiMadPageGamepadTest::toggleP2()
{
    const std::string uniq {mUniq};
    const bool on {!mP2};
    pageRequest(
        "gamepads.set_p2",
        [uniq, on](MadJson::Writer& writer) {
            writer.Key("uniq");
            writer.String(uniq.c_str(), static_cast<rapidjson::SizeType>(uniq.length()));
            writer.Key("on");
            writer.Bool(on);
        },
        [this](bool ok, const rapidjson::Value&) {
            if (ok)
                build(); // Re-layout: the P2 badge + button label change.
        });
}

bool GuiMadPageGamepadTest::onBackPressed()
{
    if (mEditMode) {
        toggleEdit();
        return true;
    }
    if (mCalMode) {
        toggleCalibrate(); // Saves + exits, like tapping CALIBRATE.
        return true;
    }
    return false;
}

bool GuiMadPageGamepadTest::input(InputConfig* config, Input input)
{
    if (mEditMode && mCanvas != nullptr) {
        if (config->isMappedTo("a", input) && input.value != 0) {
            mCanvas->cycleSelection(1);
            return true;
        }
        // Held-direction nudge (applied at repeat rate in update()).
        if (config->isMappedLike("left", input)) {
            mNudgeDx = input.value != 0 ? -1 : 0;
            return true;
        }
        if (config->isMappedLike("right", input)) {
            mNudgeDx = input.value != 0 ? 1 : 0;
            return true;
        }
        if (config->isMappedLike("up", input)) {
            mNudgeDy = input.value != 0 ? -1 : 0;
            return true;
        }
        if (config->isMappedLike("down", input)) {
            mNudgeDy = input.value != 0 ? 1 : 0;
            return true;
        }
        return true; // Swallow everything else while editing.
    }
    if (mCalMode && mCanvas != nullptr && input.value != 0) {
        if (config->isMappedLike("left", input) || config->isMappedLike("up", input)) {
            mCanvas->cycleSelection(-1);
            return true;
        }
        if (config->isMappedLike("right", input) || config->isMappedLike("down", input)) {
            mCanvas->cycleSelection(1);
            return true;
        }
        if (config->isMappedTo("a", input)) {
            const std::string spot {mCanvas->selectedKey()};
            pageRequest("tester.calibrate",
                        [spot](MadJson::Writer& writer) {
                            writer.Key("action");
                            writer.String("arm");
                            writer.Key("spot");
                            writer.String(spot.c_str(),
                                          static_cast<rapidjson::SizeType>(spot.length()));
                        },
                        nullptr);
            footer()->setStatus("Now press \"" + spot + "\" on the pad…");
            return true;
        }
        return true;
    }
    return MadLightgunPageBase::input(config, input);
}

void GuiMadPageGamepadTest::update(int deltaTime)
{
    if (mEditMode && mCanvas != nullptr && (mNudgeDx != 0 || mNudgeDy != 0)) {
        mNudgeAccum += deltaTime;
        if (mNudgeAccum >= 50) { // ±2 px hold-repeat, the Tk edit feel.
            mNudgeAccum = 0;
            mCanvas->nudgeSelected(static_cast<float>(mNudgeDx) * 2.0f,
                                   static_cast<float>(mNudgeDy) * 2.0f);
        }
    }
    MadLightgunPageBase::update(deltaTime);
}
