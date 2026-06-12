//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadCaptureModal.cpp
//
//  MAD control panel: press-to-identify / press-a-combo modal (deck-patches).
//

#include "guis/mad/GuiMadCaptureModal.h"

#include "Sound.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadTheme.h"

namespace
{
    constexpr int ERROR_DISPLAY_MS {2200};
} // namespace

GuiMadCaptureModal::GuiMadCaptureModal(GuiMadPanel* panel,
                                       const std::string& mode,
                                       const std::string& prompt,
                                       const ResultCallback& callback)
    : mRenderer {Renderer::getInstance()}
    , mPanel {panel}
    , mBackend {panel->getBackend()}
    , mCallback {callback}
    , mPrompt {prompt}
    , mFinished {false}
    , mArmed {false}
    , mCloseTimer {0}
    , mAliveToken {std::make_shared<int>(0)}
{
    const float width {std::round(mRenderer->getScreenWidth() * 0.44f)};
    const float height {std::round(mRenderer->getScreenHeight() * 0.26f)};
    setSize(width, height);
    setPosition(std::round((mRenderer->getScreenWidth() - width) / 2.0f),
                std::round((mRenderer->getScreenHeight() - height) / 2.0f));

    addChild(&mBackground);
    mBackground.fitTo(mSize);

    const float padding {width * 0.06f};
    mMessage = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_MEDIUM),
                                               MadTheme::color(MadColor::Primary), ALIGN_CENTER, ALIGN_CENTER,
                                               glm::ivec2 {0, 1});
    mMessage->setPosition(padding, height * 0.16f);
    mMessage->setSize(width - padding * 2.0f, 0.0f);
    addChild(mMessage.get());

    // The busy box carries the "Preparing…" phase; the prompt replaces it once
    // the stream reports {ready:true} (a press before that would be missed).
    mBusy.setText("Preparing the capture…");
    mBusy.setPosition(0.0f, height * 0.55f);
    mBusy.setSize(width, height * 0.3f);
    mBusy.onSizeChanged();
    addChild(&mBusy);

    // Start the capture. The stream token arrives in the response; pushes that
    // raced ahead of it are stashed by MadBackend and delivered right after
    // setStreamCallback() (see deliverUnclaimedStreams()).
    std::weak_ptr<int> alive {mAliveToken};
    mBackend->request(
        "capture.button",
        [mode](MadJson::Writer& writer) {
            writer.Key("mode");
            writer.String(mode.c_str(), static_cast<rapidjson::SizeType>(mode.length()));
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (!ok) {
                failSoon("Couldn't start the capture: " +
                         MadJson::getString(payload, "message", "unknown error"));
                return;
            }
            mStreamToken = MadJson::getString(payload, "stream");
            if (mStreamToken.empty()) {
                failSoon("The backend returned no capture stream");
                return;
            }
            mBackend->setStreamCallback(mStreamToken,
                                        [this, alive](const rapidjson::Value& data) {
                                            if (alive.expired())
                                                return;
                                            onStreamData(data);
                                        });
        });
}

GuiMadCaptureModal::~GuiMadCaptureModal()
{
    // finish() already unsubscribed on the normal paths; belt-and-braces for
    // any other teardown (e.g. the whole window stack being torn down).
    if (!mStreamToken.empty())
        mBackend->clearStreamCallback(mStreamToken);
}

void GuiMadCaptureModal::onStreamData(const rapidjson::Value& data)
{
    if (mFinished || mCloseTimer > 0) {
        // Result/timeout already handled; the trailing {closed:true} (and
        // anything else) is uninteresting now.
        return;
    }

    if (MadJson::getBool(data, "ready", false)) {
        // The evdev nodes are open and listening — arm the real prompt (a
        // press before this event would have been missed). From here B is a
        // capturable face button, so there is no cancel gesture: say so.
        mArmed = true;
        setMessage(mPrompt + " — auto-cancels in 15s", false);
        return;
    }

    if (data.IsObject() && data.HasMember("held")) {
        mResult.held.clear();
        mResult.names.clear();
        const rapidjson::Value& held {MadJson::getMember(data, "held")};
        if (held.IsArray()) {
            for (rapidjson::SizeType i {0}; i < held.Size(); ++i) {
                if (held[i].IsInt())
                    mResult.held.emplace_back(held[i].GetInt());
            }
        }
        const rapidjson::Value& names {MadJson::getMember(data, "names")};
        if (names.IsArray()) {
            for (rapidjson::SizeType i {0}; i < names.Size(); ++i) {
                if (names[i].IsString())
                    mResult.names.emplace_back(names[i].GetString());
            }
        }
        const rapidjson::Value& device {MadJson::getMember(data, "device")};
        if (device.IsObject()) {
            mResult.hasDevice = true;
            mResult.deviceName = MadJson::getString(device, "name");
            mResult.devicePinId = MadJson::getString(device, "pin_id");
            mResult.devicePinKind = MadJson::getString(device, "pin_kind");
            mResult.devicePort = MadJson::getString(device, "port");
            mResult.deviceLabel = MadJson::getString(device, "label");
        }
        NavigationSounds::getInstance().playThemeNavigationSound(SELECTSOUND);
        finish(true);
        return;
    }

    if (MadJson::getBool(data, "timeout", false)) {
        failSoon("Timed out — nothing captured");
        return;
    }

    const std::string error {MadJson::getString(data, "error")};
    if (!error.empty()) {
        failSoon(error);
        return;
    }

    if (MadJson::getBool(data, "closed", false)) {
        // Closed without a result (daemon teardown or a superseding capture).
        finish(false);
        return;
    }
}

void GuiMadCaptureModal::setMessage(const std::string& text, const bool busy)
{
    mBusy.setVisible(busy);
    mMessage->setText(text);
}

void GuiMadCaptureModal::failSoon(const std::string& text)
{
    setMessage(text, false);
    mCloseTimer = ERROR_DISPLAY_MS;
}

void GuiMadCaptureModal::finish(const bool produceResult)
{
    if (mFinished)
        return;
    mFinished = true;

    if (!mStreamToken.empty()) {
        mBackend->clearStreamCallback(mStreamToken);
        mStreamToken.clear();
    }

    // Pop first, then deliver (the GuiMsgBox deleteMeAndCall pattern): the
    // callback may push another gui or trigger requests on the page below.
    const ResultCallback callback {mCallback};
    const Result result {mResult};
    delete this;

    if (callback)
        callback(produceResult ? &result : nullptr);
}

bool GuiMadCaptureModal::input(InputConfig* config, Input input)
{
    // The captured press also reaches SDL — swallow EVERYTHING so it can't
    // activate the UI underneath (or this modal's own future buttons). B only
    // cancels BEFORE the stream is armed: once {ready:true} has arrived, B
    // (BTN_EAST 0x131) is itself a capturable face button, so it's swallowed
    // too — the daemon's 15s timeout (or the result) ends the capture.
    if (!mArmed && input.value != 0 && config->isMappedTo("b", input)) {
        NavigationSounds::getInstance().playThemeNavigationSound(BACKSOUND);
        // Fire-and-forget: the daemon stops the stream and releases the lock.
        mBackend->request("capture.cancel", nullptr, nullptr);
        finish(false);
    }
    return true;
}

void GuiMadCaptureModal::update(int deltaTime)
{
    // Window only updates the TOP gui, so while this modal is up the panel's
    // update() — and with it MadBackend::poll() — does not run. Poll from here
    // instead; the panel's own poll resumes once we pop (drains a queue, so a
    // double poll in any transition frame is harmless).
    //
    // poll() can synchronously deliver this capture's terminal stream push:
    // onStreamData() → finish() → `delete this` — guard every member access
    // after the poll behind the alive token.
    std::weak_ptr<int> alive {mAliveToken};
    mBackend->poll();
    if (alive.expired())
        return;

    if (mCloseTimer > 0) {
        mCloseTimer -= deltaTime;
        if (mCloseTimer <= 0) {
            finish(false);
            return;
        }
    }

    GuiComponent::update(deltaTime);
}

void GuiMadCaptureModal::render(const glm::mat4& parentTrans)
{
    // Window renders only the bottom gui + the top one, so with this modal
    // topmost the panel in between would vanish (the modal would float over
    // the blurred gamelist). Draw the panel first — it paints an opaque
    // backdrop, so ordering is safe — then this modal's own frame.
    if (mPanel != nullptr)
        mPanel->render(parentTrans);
    GuiComponent::render(parentTrans);
}

std::vector<HelpPrompt> GuiMadCaptureModal::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    prompts.push_back(HelpPrompt("b", "cancel"));
    return prompts;
}
