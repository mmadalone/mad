//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageXArcade.cpp
//
//  MAD control panel: X-Arcade tester (deck-patches).
//

#include "guis/mad/pages/GuiMadPageXArcade.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"

namespace
{
    // Spot key → sprite STEM in icons/x-arcade-tester/ (Tk _xat_spot_sprite +
    // _xat_load_sprites file table, by stem).
    std::string spriteStemFor(const std::string& key)
    {
        if (key.size() > 6 && key.compare(key.size() - 6, 6, "_stick") == 0)
            return "joystickrest"; // Handled as a stick item, not here.
        if (key.rfind("p1_b", 0) == 0 || key.rfind("p2_b", 0) == 0 ||
            key == "p1_coin" || key == "p2_coin" ||
            // the two trackball mouse buttons have no dedicated art → generic pressed.
            key == "mouse_l" || key == "mouse_r")
            return "pressed button";
        if (key == "p1")
            return "P1pressed";
        if (key == "p2")
            return "P2pressed";
        if (key == "mouse3")
            return "redbuttonpressed";
        if (key == "trackball")
            return "trackballiactivity";
        if (key == "side_l1" || key == "side_l2")
            return "LSidebuttonpressed";
        if (key == "side_r1" || key == "side_r2")
            return "RSidebuttonpressed";
        return "";
    }

    // Backend stick tokens (lowercase) → Joystick sprite stems.
    const std::map<std::string, std::string> STICK_STEM {
        {"up", "JoystickU"},   {"down", "JoystickD"}, {"left", "JoystickL"},
        {"right", "JoystickR"}, {"ul", "JoystickUL"}, {"ur", "JoystickUR"},
        {"dl", "JoystickDL"},  {"dr", "JoystickDR"},  {"rest", "joystickrest"}};
} // namespace

GuiMadPageXArcade::GuiMadPageXArcade(GuiMadPanel* panel)
    : MadLightgunPageBase {panel, "X-ARCADE TESTER"}
    , mRunning {false}
    , mEditMode {false}
    , mCalMode {false}
    , mPreviewAll {false}
    , mNudgeDx {0}
    , mNudgeDy {0}
    , mNudgeAccum {0}
    , mModePollAccum {0}
{
}

GuiMadPageXArcade::~GuiMadPageXArcade()
{
    if (!mStreamToken.empty())
        backend()->clearStreamCallback(mStreamToken);
    if (mRunning)
        backend()->request("tester.stop", nullptr, nullptr);
}

void GuiMadPageXArcade::build()
{
    setLoadingText("Loading the cabinet overlay…");
    pageRequest("xarcade.layout", nullptr,
                [this](bool ok, const rapidjson::Value& payload) {
                    setLoadingText("");
                    if (!ok) {
                        footer()->setStatus(
                            "Couldn't load the overlay: " +
                                MadJson::getString(payload, "message", "unknown error"),
                            true);
                        return;
                    }
                    rebuild(payload);
                },
                10000);
}

void GuiMadPageXArcade::rebuild(const rapidjson::Value& layout)
{
    // Drop our extra button ref BEFORE clearColumn so the old button dies
    // while its parent scroll view is still alive (self-detach order); also
    // covers the overlay-missing early return (no stale toggle state).
    mStartButton.reset();
    mStartRow = -1;
    beginColumn();

    const bool xbox {MadJson::getBool(layout, "xbox_mode")};
    mModeLine = std::make_shared<TextComponent>(
        xbox ? "●  Xbox 360 mode  (gamepad + trackball detected)" :
               "○  Not in gamepad mode — set the X-Arcade to Xbox 360 mode (or it's "
               "unplugged)",
        Font::get(FONT_SIZE_SMALL), xbox ? MadTheme::color(MadColor::Green) : MadTheme::color(MadColor::Red), ALIGN_LEFT,
        ALIGN_CENTER, glm::ivec2 {0, 1});
    mModeLine->setPosition(0.0f, mY);
    mModeLine->setSize(mScroll->getSize().x, 0.0f);
    mScroll->addChild(mModeLine.get());
    mWidgets.emplace_back(mModeLine);
    mY += mModeLine->getSize().y + Font::get(FONT_SIZE_SMALL)->getHeight() * 0.3f;

    const rapidjson::Value& sprites {MadJson::getMember(layout, "sprites")};
    const std::string overlay {MadJson::getString(layout, "overlay")};
    if (overlay.empty()) {
        addBlock("(overlay not found — put x-arcade-tester-overlay.png in the active "
                 "theme's router-config/icons/)",
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), 0.0f);
        endColumn();
        return;
    }
    // Buttons FIRST (true wrapped height), then pushed to the bottom so the
    // cabinet overlay gets everything in between.
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    const float contentTop {mY};
    const size_t controlsBefore {mControls.size()};
    // ONE start/stop toggle (label flips live in applyRunState). Always BUILT
    // with the wider label so the row's wrap geometry never changes.
    auto buttons = addButtonRow(
        {{"START TEST",
          [this] {
              if (mRunning)
                  pageRequest("tester.stop", nullptr, nullptr);
              else
                  startTest();
          }},
         {"CALIBRATE", [this] { toggleCalibrate(); }},
         {"EDIT POSITIONS", [this] { toggleEdit(); }},
         {"SAVE LAYOUT", [this] { savePositions(); }},
         {"PREVIEW SPRITES", [this] { togglePreview(); }}});
    mStartButton = buttons.empty() ? nullptr : buttons.front();
    mStartRow = mControls[controlsBefore].row;
    if (mStartButton != nullptr) {
        mStartButtonWidth = mStartButton->getSize().x;
        applyRunState(); // Mid-test rebuilds re-enter with mRunning == true.
    }
    const float rowHeight {mY - contentTop};
    const float gapY {smallHeight * 0.4f};
    const float targetBottom {mViewportSize.y - smallHeight * 0.5f};
    const float availHeight {std::max(mViewportSize.y * 0.25f,
                                      targetBottom - contentTop - rowHeight - gapY)};
    moveControls(controlsBefore, availHeight + gapY);
    mY = contentTop + availHeight + gapY + rowHeight;

    mCanvas = std::make_shared<MadSpriteCanvas>();
    mCanvas->setBase(overlay);
    const glm::vec2 native {mCanvas->nativeSize()};
    const float scale {std::min(availHeight / native.y, mViewportSize.x / native.x)};
    const glm::vec2 box {native * scale};
    mCanvas->setPosition((mViewportSize.x - box.x) / 2.0f,
                         contentTop + (availHeight - box.y) / 2.0f);
    mCanvas->setSize(box.x, box.y);
    auto spritePath = [&sprites](const std::string& stem) {
        return MadJson::getString(sprites, stem.c_str());
    };
    mSpotLabels.clear();
    const rapidjson::Value& spots {MadJson::getMember(layout, "spots")};
    if (spots.IsArray()) {
        for (rapidjson::SizeType i {0}; i < spots.Size(); ++i) {
            const std::string key {MadJson::getString(spots[i], "key")};
            mSpotLabels[key] = MadJson::getString(spots[i], "label", key);
            const float nx {static_cast<float>(
                MadJson::getMember(spots[i], "x").IsNumber() ?
                    MadJson::getMember(spots[i], "x").GetDouble() :
                    0.5)};
            const float ny {static_cast<float>(
                MadJson::getMember(spots[i], "y").IsNumber() ?
                    MadJson::getMember(spots[i], "y").GetDouble() :
                    0.5)};
            if (key == "p1_stick" || key == "p2_stick") {
                std::map<std::string, std::string> images;
                for (const auto& entry : STICK_STEM) {
                    const std::string path {spritePath(entry.second)};
                    if (!path.empty())
                        images[entry.first] = path;
                }
                if (!images.empty())
                    mCanvas->addItem(key, nx, ny, images, true, "rest");
                continue;
            }
            const std::string stem {spriteStemFor(key)};
            const std::string path {stem.empty() ? "" : spritePath(stem)};
            if (!path.empty())
                mCanvas->addItem(key, nx, ny, {{"on", path}});
        }
    }
    mScroll->addChild(mCanvas.get());
    mWidgets.emplace_back(mCanvas);
    endColumn();
}

void GuiMadPageXArcade::startTest()
{
    if (mRunning)
        return;
    pageRequest(
        "tester.start",
        [](MadJson::Writer& writer) {
            writer.Key("kind");
            writer.String("xarcade");
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't start: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            mRunning = true;
            applyRunState();
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
        },
        10000);
}

void GuiMadPageXArcade::onStreamPush(const rapidjson::Value& data)
{
    if (MadJson::getBool(data, "closed")) {
        mRunning = false;
        applyRunState();
        mPressed.clear();
        mStickState.clear();
        if (mCanvas != nullptr)
            mCanvas->resetItems();
        // A manual STOP ends with closed only (no "ended" push) — drop the
        // "Testing…" sticky or it covers the help prompts forever.
        footer()->setStatus("");
        return;
    }
    if (MadJson::getBool(data, "ready")) {
        const int failed {MadJson::getInt(data, "grab_failed")};
        footer()->setStatus(
            failed ? "⚠ Captured the cab but " + std::to_string(failed) +
                         " node(s) wouldn't grab — those may still navigate." :
                     "Testing — press any control. Hold P1+P2 Start 3 s to end (or "
                     "STOP TEST with the Deck pad).",
            failed != 0);
        return;
    }
    const std::string ended {MadJson::getString(data, "ended")};
    if (!ended.empty()) {
        // Clear the sticky FIRST or the flash would restore the stale
        // "Testing…"/countdown text when it expires.
        footer()->setStatus("");
        footer()->flash(MadJson::getString(data, "message", "Stopped."), 4000);
        return;
    }
    const bool counting {data.HasMember("countdown")};
    if (counting)
        footer()->setStatus("Keep holding P1+P2 Start to end…  " +
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
    if (!counting && (spots.IsObject() || sticks.IsObject()))
        refreshLiveFooter();
    const rapidjson::Value& bound {MadJson::getMember(data, "bound")};
    if (bound.IsObject())
        footer()->setStatus("✓ bound → \"" + MadJson::getString(bound, "spot") +
                            "\". Pick the next with the d-pad + A, or CALIBRATE to save.");
}

void GuiMadPageXArcade::toggleEdit()
{
    mEditMode = !mEditMode;
    if (mEditMode && mCalMode)
        toggleCalibrate();
    if (mCanvas != nullptr) {
        mCanvas->setAllVisible(mEditMode || mPreviewAll);
        mCanvas->setSelectionVisible(mEditMode);
    }
    if (mEditMode) {
        footer()->setStatus("Edit — A picks the next sprite, d-pad nudges it, B "
                            "exits. Then SAVE LAYOUT.");
    }
    else {
        footer()->setStatus("");
        footer()->flash("Edit off.");
    }
    mNudgeDx = mNudgeDy = 0;
}

void GuiMadPageXArcade::toggleCalibrate()
{
    if (mCalMode) {
        mCalMode = false;
        if (mCanvas != nullptr) {
            mCanvas->setSelectionVisible(false);
            mCanvas->setAllVisible(mPreviewAll);
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
        pageRequest("tester.stop", nullptr, nullptr);
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
    footer()->setStatus("Calibrate — d-pad picks a spot, A arms it, then press that "
                        "control on the cabinet. CALIBRATE again to save.");
}

void GuiMadPageXArcade::togglePreview()
{
    mPreviewAll = !mPreviewAll;
    if (mCanvas != nullptr && !mEditMode && !mCalMode)
        mCanvas->setAllVisible(mPreviewAll);
    if (mPreviewAll) {
        footer()->setStatus("Previewing all sprites — check scale/positions.");
    }
    else {
        footer()->setStatus("");
        footer()->flash("Sprite preview off.");
    }
}

void GuiMadPageXArcade::savePositions()
{
    if (mCanvas == nullptr)
        return;
    auto positions = mCanvas->positions();
    pageRequest(
        "xarcade.positions_save",
        [positions](MadJson::Writer& writer) {
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
}

void GuiMadPageXArcade::applyRunState()
{
    if (mStartButton == nullptr)
        return;
    const std::string label {mRunning ? "STOP TEST" : "START TEST"};
    mStartButton->setText(label, label);
    // Pin the build-time (wider-label) width so the row never re-wraps or
    // shifts — only the text inside the button changes.
    mStartButton->setSize(std::max(mStartButtonWidth, mStartButton->getSize().x),
                          mStartButton->getSize().y);
}

void GuiMadPageXArcade::refreshLiveFooter()
{
    // Live press reporting — the Tk testers' readout line, in the footer.
    std::string live;
    for (const auto& entry : mPressed) {
        if (!entry.second)
            continue;
        const auto label = mSpotLabels.find(entry.first);
        live += (live.empty() ? "" : "   ·   ") +
                (label != mSpotLabels.end() ? label->second : entry.first);
    }
    for (const auto& stick : mStickState) {
        if (stick.second != "rest" && !stick.second.empty())
            live += std::string(live.empty() ? "" : "   ·   ") +
                    (stick.first == "p1_stick" ? "P1 stick " : "P2 stick ") + stick.second;
    }
    footer()->setStatus(live.empty() ?
                            "Testing — press any control on the cabinet. Hold P1+P2 "
                            "Start 3 s to end." :
                            live);
}

bool GuiMadPageXArcade::onBackPressed()
{
    if (mEditMode) {
        toggleEdit();
        return true;
    }
    if (mCalMode) {
        toggleCalibrate();
        return true;
    }
    return false;
}

bool GuiMadPageXArcade::input(InputConfig* config, Input input)
{
    if (mEditMode && mCanvas != nullptr) {
        if (config->isMappedTo("a", input) && input.value != 0) {
            mCanvas->cycleSelection(1);
            return true;
        }
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
        return true;
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
            footer()->setStatus("Now press \"" + spot + "\" on the cabinet…");
            return true;
        }
        return true;
    }
    return MadLightgunPageBase::input(config, input);
}

void GuiMadPageXArcade::update(int deltaTime)
{
    if (mEditMode && mCanvas != nullptr && (mNudgeDx != 0 || mNudgeDy != 0)) {
        mNudgeAccum += deltaTime;
        if (mNudgeAccum >= 50) {
            mNudgeAccum = 0;
            mCanvas->nudgeSelected(static_cast<float>(mNudgeDx) * 2.0f,
                                   static_cast<float>(mNudgeDy) * 2.0f);
        }
    }
    // Mode line: metadata-only poll, the Tk 1.5 s cadence.
    mModePollAccum += deltaTime;
    if (mModePollAccum >= 1500 && mModeLine != nullptr) {
        mModePollAccum = 0;
        pageRequest("xarcade.status", nullptr,
                    [this](bool ok, const rapidjson::Value& payload) {
                        if (!ok || mModeLine == nullptr)
                            return;
                        const bool xbox {MadJson::getBool(payload, "xbox_mode")};
                        mModeLine->setText(
                            xbox ? "●  Xbox 360 mode  (gamepad + trackball detected)" :
                                   "○  Not in gamepad mode — set the X-Arcade to Xbox "
                                   "360 mode (or it's unplugged)");
                        mModeLine->setColor(xbox ? MadTheme::color(MadColor::Green) : MadTheme::color(MadColor::Red));
                    });
    }
    MadLightgunPageBase::update(deltaTime);
}
