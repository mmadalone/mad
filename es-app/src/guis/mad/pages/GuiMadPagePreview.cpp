//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePreview.cpp
//
//  MAD control panel: live routing preview (deck-patches).
//

#include "guis/mad/pages/GuiMadPagePreview.h"

#include "Sound.h"
#include "Window.h"
#include "guis/mad/GuiMadCaptureModal.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"

#include <cmath>
#include "guis/mad/MadTheme.h"

namespace
{
    // The Tk page's visible-poll cadence for the HID-only Wiimote state.
    constexpr int WIIMOTE_POLL_MS {2000};
} // namespace

GuiMadPagePreview::GuiMadPagePreview(GuiMadPanel* panel)
    : MadPage {panel, "LIVE ROUTING PREVIEW"}
    , mRenderer {Renderer::getInstance()}
    , mTopFocus {0}
    , mBodyTop {0.0f}
    , mBodyHeight {0.0f}
    , mScrollOffset {0.0f}
    , mFullLoaded {false}
    , mRequestInFlight {false}
    , mRefreshPending {false}
    , mPendingForce {false}
    , mWiiPresent {false}
    , mWiiSlots {0}
    , mWiiCount {0}
    , mWiimotePollTimer {0}
    , mWiimotePollInFlight {false}
{
}

void GuiMadPagePreview::build()
{
    // Static, focusable top row — never rebuilt, so focus survives the
    // per-response body rebuilds below it.
    mTopButtons.emplace_back(std::make_shared<ButtonComponent>(
        "REFRESH", "refresh", [this] { requestPreview(true); }));
    mTopButtons.emplace_back(std::make_shared<ButtonComponent>(
        "IDENTIFY X-ARCADE", "identify x-arcade", [this] { identifyXarcade(); }));
    mTopButtons.emplace_back(
        std::make_shared<ButtonComponent>("CLEAR", "clear", [this] { clearXarcade(); }));

    const float gap {mViewportSize.x * 0.012f};
    float x {mViewportPos.x};
    for (auto& button : mTopButtons) {
        button->setPosition(x, mViewportPos.y);
        x += button->getSize().x + gap;
        addChild(button.get());
    }
    applyTopFocus();

    const float statusHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    mXaStatus = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                                MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER,
                                                glm::ivec2 {0, 0});
    mXaStatus->setPosition(mViewportPos.x,
                           mViewportPos.y + mTopButtons.front()->getSize().y + statusHeight * 0.3f);
    mXaStatus->setSize(mViewportSize.x, statusHeight);
    addChild(mXaStatus.get());

    mBodyTop = mXaStatus->getPosition().y + statusHeight + statusHeight * 0.5f;

    // Idempotent (the backend returns the same stream token with already:true);
    // the panel routes the pushes to whichever page is current.
    mPanel->ensureDeviceWatch();

    setLoadingText("Scanning controllers…");
    // Two-phase first load: show the connected controllers immediately from the
    // fast evdev-only scan, then fill in the SDL-ordered list + routes once the
    // slow preview.all (≈6 s cold SDL identity probe) lands.
    requestDevices();
    requestPreview(false);
}

void GuiMadPagePreview::requestDevices()
{
    pageRequest(
        "preview.devices", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            // If the full preview already rendered (cache hit raced ahead), or
            // this failed, don't overwrite it with the routes-pending partial.
            if (!ok || mFullLoaded)
                return;
            setLoadingText("");
            rebuildBody(payload);
        },
        4000);
}

void GuiMadPagePreview::requestPreview(const bool force)
{
    if (mRequestInFlight) {
        // Coalesce hotplug bursts: exactly one fresh request after this one.
        // A forced request must stay forced when the re-request goes out.
        mRefreshPending = true;
        mPendingForce = mPendingForce || force;
        return;
    }
    mRequestInFlight = true;

    pageRequest(
        "preview.all",
        [force](MadJson::Writer& writer) {
            writer.Key("force");
            writer.Bool(force);
        },
        [this](bool ok, const rapidjson::Value& payload) {
            mRequestInFlight = false;
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't build the preview: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
            }
            else {
                mFullLoaded = true;
                rebuildBody(payload);
            }
            if (mRefreshPending) {
                mRefreshPending = false;
                const bool pendingForce {mPendingForce};
                mPendingForce = false;
                requestPreview(pendingForce);
            }
        },
        10000);
}

void GuiMadPagePreview::addBodyLine(const float x,
                                    float& y,
                                    const float width,
                                    const std::string& text,
                                    const unsigned int color,
                                    const std::string& iconPath,
                                    const float iconWidth,
                                    const float iconHeight)
{
    const float lineHeight {Font::get(FONT_SIZE_SMALL)->getHeight() * 1.12f};
    float textX {x};
    float textWidth {width};
    float rowHeight {lineHeight};

    if (!iconPath.empty() && iconWidth > 0.0f && iconHeight > 0.0f) {
        // Tall icons grow the row; the image letterboxes into the box and is
        // anchored left-center so text and art share the row's vertical middle.
        rowHeight = std::max(lineHeight, iconHeight * 1.1f);
        auto image = std::make_shared<ImageComponent>();
        image->setOrigin(0.0f, 0.5f);
        image->setMaxSize(iconWidth, iconHeight);
        image->setImage(iconPath);
        image->setPosition(x, y + rowHeight / 2.0f);
        mBodyImages.emplace_back(image);
        const float gap {iconWidth * 0.15f};
        textX = x + iconWidth + gap;
        textWidth = std::max(0.0f, width - iconWidth - gap);
    }

    // Auto-calc the text WIDTH ({1,0}) so the wrap-width is 0 and the line never
    // wraps. The icon rows are taller than one line, which otherwise makes ES-DE's
    // TextComponent treat the box as multi-line (mSize.y > lineHeight) and wrap a
    // long label like "DualShock 4  🔋35%" onto a 2nd line. setSize still sets the
    // row height so the single line stays vertically centered against the icon.
    auto line = std::make_shared<TextComponent>(text, Font::get(FONT_SIZE_SMALL), color,
                                                ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {1, 0});
    line->setPosition(textX, y);
    line->setSize(textWidth, rowHeight);
    mBodyLines.emplace_back(line);
    y += rowHeight;
}

void GuiMadPagePreview::rebuildBody(const rapidjson::Value& result)
{
    mDolphinLine.reset();
    mBodyLines.clear();
    mBodyImages.clear();

    // Icon boxes at 2× the Tk page's sizes (laid out for 800p) — the Tk sizes
    // read too small on the TV (user request 2026-06-12).
    const float px {Renderer::getScreenHeight() / 800.0f};
    const float padIconSize {88.0f * px}; // Connected-controller rows.
    const float rowIconSize {60.0f * px}; // Per-route pad rows.
    const float artWidth {80.0f * px}; // Console art boxes (route headers).
    const float artHeight {52.0f * px};

    // The fast first phase (preview.devices) carries controllers only — no
    // routes/wiimotes yet. Drive the partial rendering off what's present.
    const rapidjson::Value& routes {MadJson::getMember(result, "routes")};
    const bool routesReady {routes.IsArray()};
    const rapidjson::Value& wiimotes {MadJson::getMember(result, "wiimotes")};
    const bool wiiReady {wiimotes.IsObject()};

    const std::string xport {MadJson::getString(result, "xport")};
    mXaStatus->setText(
        !xport.empty() ?
            "X-Arcade = USB port " + xport :
            "X-Arcade: not identified — 045e pads shown as Xbox 360 until then");

    const float leftX {0.0f};
    const float rightX {mViewportSize.x * 0.52f};
    const float colWidth {mViewportSize.x * 0.48f};
    float leftY {0.0f};
    float rightY {0.0f};

    // LEFT — connected controllers. SDL order once preview.all lands; the fast
    // phase shows them in evdev order (index -1 → no "SDL-N" prefix).
    addBodyLine(leftX, leftY, colWidth,
                routesReady ? "Connected controllers (SDL order):" : "Connected controllers:",
                MadTheme::color(MadColor::Title));
    const rapidjson::Value& controllers {MadJson::getMember(result, "controllers")};
    if (!controllers.IsArray() || controllers.Size() == 0) {
        addBodyLine(leftX, leftY, colWidth, "  (none detected)", MadTheme::color(MadColor::Secondary));
    }
    else {
        for (rapidjson::SizeType i {0}; i < controllers.Size(); ++i) {
            const rapidjson::Value& pad {controllers[i]};
            const int idx {MadJson::getInt(pad, "index", -1)};
            std::string text {idx >= 0 ? "SDL-" + std::to_string(idx) + "  " : ""};
            text += MadJson::getString(pad, "vidpid") + "  " +
                    MadJson::getString(pad, "label", MadJson::getString(pad, "name"));
            const rapidjson::Value& battery {MadJson::getMember(pad, "battery")};
            if (battery.IsObject()) {
                const int pct {MadJson::getInt(battery, "pct", -1)};
                if (pct >= 0) {
                    text.append("  🔋").append(std::to_string(pct)).append("%");
                    if (MadJson::getString(battery, "status") == "Charging")
                        text.append(" ⚡");
                    else if (pct <= 20)
                        text.append(" ⚠");
                }
            }
            addBodyLine(leftX, leftY, colWidth, text, MadTheme::color(MadColor::Primary),
                        MadJson::getString(pad, "icon"), padIconSize, padIconSize);
        }
    }

    // DolphinBar status, derived from the wiimotes object (Tk _preview_route's
    // dolphin branch + the Wii label). The line is kept addressable so the
    // 2-second Wiimote poll can patch it in place between previews.
    leftY += Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f;
    addBodyLine(leftX, leftY, colWidth, "", MadTheme::color(MadColor::Secondary),
                MadJson::getString(wiimotes, "icon"), artWidth * 2.0f, padIconSize);
    mDolphinLine = mBodyLines.back();
    if (wiiReady) {
        mWiiPresent = MadJson::getBool(wiimotes, "present", false);
        mWiiSlots = MadJson::getInt(wiimotes, "slots", 0);
        mWiiCount = MadJson::getInt(wiimotes, "count", 0);
        applyDolphinLine();
    }
    else {
        mDolphinLine->setText("Checking Wii Remotes…");
        mDolphinLine->setColor(MadTheme::color(MadColor::Secondary));
    }

    // RIGHT — the would-route preview per routed system/collection. Pending
    // until the slow preview.all resolves it (the fast phase has no routes).
    addBodyLine(rightX, rightY, colWidth, "Would route (read-only preview):", MadTheme::color(MadColor::Title));
    if (!routesReady) {
        addBodyLine(rightX, rightY, colWidth, "  Resolving routes…",
                    MadTheme::color(MadColor::Secondary));
    }
    if (routesReady) {
        for (rapidjson::SizeType i {0}; i < routes.Size(); ++i) {
            const rapidjson::Value& entry {routes[i]};
            addBodyLine(rightX, rightY, colWidth,
                        MadJson::getString(entry, "label", MadJson::getString(entry, "key")),
                        MadTheme::color(MadColor::Title), MadJson::getString(entry, "art"), artWidth,
                        artHeight);
            const rapidjson::Value& route {MadJson::getMember(entry, "route")};
            if (MadJson::getString(route, "kind") == "pads") {
                const rapidjson::Value& rows {MadJson::getMember(route, "rows")};
                if (rows.IsArray()) {
                    for (rapidjson::SizeType j {0}; j < rows.Size(); ++j) {
                        std::string text {"  " + MadJson::getString(rows[j], "slot") + "  " +
                                          MadJson::getString(rows[j], "text")};
                        if (MadJson::getBool(rows[j], "pinned", false))
                            text.append(" 📌");
                        addBodyLine(rightX, rightY, colWidth, text, MadTheme::color(MadColor::Primary),
                                    MadJson::getString(rows[j], "icon_path"), rowIconSize,
                                    rowIconSize);
                    }
                }
            }
            else {
                addBodyLine(rightX, rightY, colWidth,
                            "  " + MadJson::getString(route, "text", "(no preview)"),
                            MadTheme::color(MadColor::Secondary));
            }
            rightY += Font::get(FONT_SIZE_SMALL)->getHeight() * 0.35f;
        }
    }

    mBodyHeight = std::max(leftY, rightY);
    const float viewHeight {mViewportPos.y + mViewportSize.y - mBodyTop};
    mScrollOffset = glm::clamp(mScrollOffset, 0.0f, std::max(0.0f, mBodyHeight - viewHeight));
}

void GuiMadPagePreview::applyTopFocus()
{
    for (size_t i {0}; i < mTopButtons.size(); ++i) {
        if (static_cast<int>(i) == mTopFocus)
            mTopButtons[i]->onFocusGained();
        else
            mTopButtons[i]->onFocusLost();
    }
}

void GuiMadPagePreview::identifyXarcade()
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiMadCaptureModal(
        mPanel, "identify", "Press a button on your X-Arcade…",
        [this, alive](const GuiMadCaptureModal::Result* result) {
            if (alive.expired() || result == nullptr)
                return;
            if (result->devicePort.empty()) {
                footer()->flash(
                    "Couldn't read a USB port for that pad — use the wired X-Arcade.", 4000,
                    true);
                return;
            }
            const std::string port {result->devicePort};
            pageRequest(
                "policy.set_hardware",
                [port](MadJson::Writer& writer) {
                    writer.Key("key");
                    writer.String("xarcade_port");
                    writer.Key("value");
                    writer.String(port.c_str(),
                                  static_cast<rapidjson::SizeType>(port.length()));
                },
                [this, port](bool ok, const rapidjson::Value& payload) {
                    if (!ok) {
                        footer()->flash("Couldn't save the X-Arcade port: " +
                                            MadJson::getString(payload, "message",
                                                               "unknown error"),
                                        4000, true);
                        return;
                    }
                    footer()->flash("X-Arcade set to USB port " + port);
                    requestPreview(false);
                });
        }));
}

void GuiMadPagePreview::clearXarcade()
{
    pageRequest(
        "policy.clear_hardware",
        [](MadJson::Writer& writer) {
            writer.Key("key");
            writer.String("xarcade_port");
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't clear the X-Arcade port: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            footer()->flash(
                "X-Arcade port cleared — 045e pads shown as Xbox 360 until you Identify.");
            requestPreview(false);
        });
}

void GuiMadPagePreview::onDevicesChanged(const rapidjson::Value& data)
{
    // Hotplug: re-preview from the fresh device set (never force — the cached
    // Wiimote probe is fine; the REFRESH button busts it explicitly).
    requestPreview(false);
}

void GuiMadPagePreview::applyDolphinLine()
{
    if (mDolphinLine == nullptr)
        return;
    if (!mWiiPresent) {
        mDolphinLine->setText("⚠ no DolphinBar connected");
        mDolphinLine->setColor(MadTheme::color(MadColor::Secondary));
    }
    else if (mWiiSlots == 0) {
        mDolphinLine->setText(
            "⚠ DolphinBar connected but exposing 0 slots — re-plug its USB");
        mDolphinLine->setColor(MadTheme::color(MadColor::Red));
    }
    else {
        mDolphinLine->setText("DolphinBar Wii Remotes: " + std::to_string(mWiiCount));
        mDolphinLine->setColor(MadTheme::color(MadColor::Primary));
    }
}

void GuiMadPagePreview::pollWiimotes()
{
    mWiimotePollInFlight = true;
    pageRequest(
        "devices.wiimotes",
        [](MadJson::Writer& writer) {
            writer.Key("force");
            writer.Bool(true);
        },
        [this](bool ok, const rapidjson::Value& payload) {
            mWiimotePollInFlight = false;
            if (!ok)
                return;
            const bool present {MadJson::getBool(payload, "present", false)};
            const int slots {MadJson::getInt(payload, "slots", 0)};
            const int count {MadJson::getInt(payload, "count", 0)};
            if (present == mWiiPresent && slots == mWiiSlots && count == mWiiCount)
                return;
            mWiiPresent = present;
            mWiiSlots = slots;
            mWiiCount = count;
            // Patch the DolphinBar line in place for instant feedback, then
            // re-preview (non-forced — the forced probe above just refreshed
            // the backend's cache) so the wii system's route line follows.
            applyDolphinLine();
            requestPreview(false);
        },
        10000);
}

void GuiMadPagePreview::update(int deltaTime)
{
    // Wiimote sync/drop is HID-only — no evdev node appears or disappears, so
    // devices.watch never fires for it. Mirror the Tk page's 2-second poll
    // while the page is visible; suspended while a capture stream holds the
    // input lock (the probe would fight the capture for the daemon's worker).
    if (!mPanel->isInputLocked() && !mWiimotePollInFlight) {
        mWiimotePollTimer += deltaTime;
        if (mWiimotePollTimer >= WIIMOTE_POLL_MS) {
            mWiimotePollTimer = 0;
            pollWiimotes();
        }
    }
    GuiComponent::update(deltaTime);
}

bool GuiMadPagePreview::input(InputConfig* config, Input input)
{
    if (input.value == 0)
        return false;

    if (config->isMappedLike("left", input)) {
        if (mTopFocus > 0) {
            --mTopFocus;
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            applyTopFocus();
        }
        return true;
    }
    if (config->isMappedLike("right", input)) {
        if (mTopFocus < static_cast<int>(mTopButtons.size()) - 1) {
            ++mTopFocus;
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            applyTopFocus();
        }
        return true;
    }
    if (config->isMappedTo("a", input) && mTopFocus < static_cast<int>(mTopButtons.size()))
        return mTopButtons[mTopFocus]->input(config, input);

    return false;
}

void GuiMadPagePreview::pageScroll(int direction)
{
    const float viewHeight {mViewportPos.y + mViewportSize.y - mBodyTop};
    if (mBodyHeight <= viewHeight)
        return;
    mScrollOffset = glm::clamp(mScrollOffset + static_cast<float>(direction) * viewHeight * 0.85f,
                               0.0f, std::max(0.0f, mBodyHeight - viewHeight));
}

void GuiMadPagePreview::render(const glm::mat4& parentTrans)
{
    glm::mat4 trans {parentTrans * getTransform()};
    renderChildren(trans); // Title, top row, status line, loading text.

    if (mBodyLines.empty())
        return;

    // Clip the body strip below the top row (scale-aware, like MadTileGrid).
    const float viewHeight {mViewportPos.y + mViewportSize.y - mBodyTop};
    glm::mat4 bodyTrans {
        glm::translate(trans, glm::vec3 {mViewportPos.x, mBodyTop, 0.0f})};
    glm::vec3 dim {mViewportSize.x, viewHeight, 0.0f};
    dim.x = (bodyTrans[0].x * dim.x + bodyTrans[3].x) - bodyTrans[3].x;
    dim.y = (bodyTrans[1].y * dim.y + bodyTrans[3].y) - bodyTrans[3].y;
    mRenderer->pushClipRect(
        glm::ivec2 {static_cast<int>(std::round(bodyTrans[3].x)),
                    static_cast<int>(std::round(bodyTrans[3].y))},
        glm::ivec2 {static_cast<int>(std::round(dim.x)), static_cast<int>(std::round(dim.y))});

    glm::mat4 scrolledTrans {glm::translate(bodyTrans, glm::vec3 {0.0f, -mScrollOffset, 0.0f})};
    for (auto& line : mBodyLines) {
        const float lineTop {line->getPosition().y};
        if (lineTop + line->getSize().y < mScrollOffset || lineTop > mScrollOffset + viewHeight)
            continue;
        line->render(scrolledTrans);
    }
    for (auto& image : mBodyImages) {
        // Left-center anchored (origin {0, 0.5}): position.y is the row middle.
        const float imageTop {image->getPosition().y - image->getSize().y / 2.0f};
        if (imageTop + image->getSize().y < mScrollOffset ||
            imageTop > mScrollOffset + viewHeight)
            continue;
        image->render(scrolledTrans);
    }

    mRenderer->popClipRect();
}

std::vector<HelpPrompt> GuiMadPagePreview::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    prompts.push_back(HelpPrompt("left/right", "choose"));
    prompts.push_back(HelpPrompt("a", "select"));
    prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void GuiMadPagePreview::onSaveFocus()
{
    mFocusCookie = mTopFocus;
}

void GuiMadPagePreview::onRestoreFocus()
{
    mTopFocus = glm::clamp(mFocusCookie, 0, static_cast<int>(mTopButtons.size()) - 1);
    applyTopFocus();
}
