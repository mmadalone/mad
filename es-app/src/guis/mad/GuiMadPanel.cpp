//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPanel.cpp
//
//  MAD control panel (deck-patches): fullscreen, ES-DE-native shell around the
//  mad-backend.py daemon.
//

#include "guis/mad/GuiMadPanel.h"

#include "Sound.h"
#include "guis/GuiMsgBox.h"
#include "guis/mad/MadWiiBridge.h"
#include "guis/mad/pages/GuiMadPageBackup.h"
#include "guis/mad/pages/GuiMadPageGamepads.h"
#include "guis/mad/pages/GuiMadPageLightgun.h"
#include "guis/mad/pages/GuiMadPagePlayers.h"
#include "guis/mad/pages/GuiMadPagePreview.h"
#include "guis/mad/pages/GuiMadPageQuitCombo.h"
#include "guis/mad/pages/GuiMadPageSplash.h"
#include "guis/mad/pages/GuiMadPageXArcade.h"
#include "guis/mad/pages/GuiMadPageStandalones.h"
#include "guis/mad/pages/GuiMadPageStandaloneSections.h"
#include "guis/mad/pages/GuiMadPageSidebar.h"
#include "guis/mad/MadTheme.h"

#include <algorithm>

GuiMadPanel::GuiMadPanel()
    : mRenderer {Renderer::getInstance()}
    , mCurrentSection {0}
    , mStateEpoch {0}
    , mPanelState {PanelState::Connecting}
    , mSidebarWidth {0.0f}
    , mHelpReserve {0.0f}
    , mInputLocked {false}
    , mInputLockAllowNav {false}
    , mSidebarBuilt {false}
{
    setPosition(0.0f, 0.0f);
    setSize(Renderer::getScreenWidth(), Renderer::getScreenHeight());

    // MAD theming: (re)load the active theme's router-config/*-theme.xml
    // BEFORE any panel widget is created. The injected defaults are the
    // CURRENT menu-scheme values (these statics are protected, and reading
    // them here also tracks the dark/light scheme).
    MadTheme::getInstance().load({
        {MadColor::Frame, mMenuColorFrame},
        {MadColor::Primary, mMenuColorPrimary},
        {MadColor::Secondary, mMenuColorSecondary},
        {MadColor::Title, mMenuColorTitle},
        {MadColor::Selector, mMenuColorSelector},
        {MadColor::Red, mMenuColorRed},
        {MadColor::Green, mMenuColorGreen},
        {MadColor::Separators, mMenuColorSeparators},
        {MadColor::PanelDimmed, mMenuColorPanelDimmed},
        {MadColor::ButtonFlatUnfocused, mMenuColorButtonFlatUnfocused},
        {MadColor::HelpText, 0x777777FF},
        // Scheme-locked focus/selection cues (never theme-overridable). Highlight
        // is the scheme selector (a FILL); MadTheme::color() special-cases it to a
        // legible backdrop under the light scheme. HighlightAccent is the scheme
        // red, for thin outlines + focused text (visible on both light and dark).
        {MadColor::Highlight, mMenuColorSelector},
        {MadColor::HighlightAccent, mMenuColorRed},
    });

    // Section registry — every section is native (the classic Tk control
    // panel was retired in phase 5B; router-config-gui.py stays in the repo
    // as the behavioral reference, it just isn't launched anymore).
    // Backends / Daphne / Model 2 are now reached through the Standalones hub
    // (each emulator's tile), so they're no longer top-level sidebar sections.
    mSections = {{"Preview", "preview"},
                 {"Device pins", "players"},
                 {"Quit combo", "quit-combo"},
                 {"Lightgun", "lightgun"},
                 {"Standalones", "standalones"}, {"RetroArch", "retroarch"},
                 {"X-Arcade", "x-arcade"},
                 {"Gamepads", "gamepads"}, {"Splash", "splash"},
                 {"Backup", "backup"},
                 {"Sidebar", "sidebar"}};
    mAllSections = mSections;             // master set; mSections gets filtered to the visible rows
    mSavedRoots.resize(mSections.size()); // one kept-alive root page per section

    const float padding {mSize.y * 0.025f};
    // ES-DE's help row at the very bottom of the screen — shared with the
    // footer: while the footer has text the panel suppresses the prompts.
    mHelpReserve = mSize.y * 0.055f;
    refreshThemedBackground();

    std::vector<std::string> labels;
    for (const Section& section : mSections)
        labels.emplace_back(section.label);

    // Sidebar hugs its content (icon box or the widest label, plus margins)
    // instead of a fixed 14% column with dead space at the sides.
    float maxLabelWidth {0.0f};
    for (const std::string& label : labels)
        maxLabelWidth =
            std::max(maxLabelWidth, Font::get(FONT_SIZE_MINI)->sizeText(label).x);
    const float iconBox {Renderer::getScreenHeight() * 0.14f};
    const float sidebarMargin {Renderer::getScreenHeight() * 0.024f};
    mSidebarWidth =
        std::min(mSize.x * 0.14f, std::max(iconBox, maxLabelWidth) + sidebarMargin);

    // The sidebar WIDGET itself is deliberately NOT built here. Building it now would paint
    // the hardcoded default mSections order for one frame during Connecting, then visibly
    // snap to a saved (rearranged) SIDEBAR_ORDER the moment the async sidebar.sections reply
    // lands — the bug this defers to fix. Instead the widget is born for the first time
    // inside applySidebarVisibility() (see onBackendReady()/requestSidebarVisibility()),
    // already in the saved order, behind the busy spinner. requestSidebarIcons() primes the
    // same themed-icon precedence at that point, so nothing is lost by not doing it here.

    mFooter = std::make_unique<MadFooter>();
    mFooter->setPosition(0.0f, mSize.y - mHelpReserve);
    mFooter->setSize(mSize.x, mHelpReserve);
    // While the footer has text the prompts yield the strip (and return the
    // moment it clears) — one fully dynamic help row.
    mFooter->setOnVisibilityChanged([this] { updateHelpPrompts(); });
    addChild(mFooter.get());

    // Our own help row sits on the themed strip; color it like the footer.
    mStripHelp.setHelpTextColor(MadTheme::color(MadColor::HelpText));
    mStripHelp.setHelpIconColor(MadTheme::color(MadColor::HelpText));
    // Private glyph-icon cache: the Window's normal help bar ALSO renders the
    // panel's prompts (MAD is the top GUI) and shares the same cached icon
    // objects; on button/scroll pages it re-sized those shared icons to a
    // degenerate size, blanking our strip's glyphs. An isolated cache means
    // only this strip mutates its own icons. (Root cause of the blank glyphs.)
    mStripHelp.setUseLocalIconCache(true);

    mContentPos = {mSidebarWidth + padding, padding};
    mContentSize = {mSize.x - mSidebarWidth - padding * 2.0f,
                    mSize.y - mHelpReserve - padding * 2.0f};

    // Connecting/error status; rendered manually per panel state (not a child).
    mStatusText = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_MEDIUM),
                                                  MadTheme::color(MadColor::Primary), ALIGN_CENTER, ALIGN_CENTER,
                                                  glm::ivec2 {0, 1});
    mStatusText->setPosition(mContentPos.x + mContentSize.x * 0.1f,
                             mContentPos.y + mContentSize.y * 0.25f);
    mStatusText->setSize(mContentSize.x * 0.8f, 0.0f);

    mRetryButton = std::make_shared<ButtonComponent>("RETRY", "retry", [this] {
        mBackend->restart();
        showConnecting();
    });
    mRetryButton->onFocusGained();

    mBusy.setPosition(mContentPos.x, mContentPos.y);
    mBusy.setSize(mContentSize.x, mContentSize.y);
    mBusy.setText("Starting MAD backend…");
    mBusy.onSizeChanged();

    // Preview is native as of phase 1 — restore the spec-order landing. (No
    // mSidebar->setActive() here: the widget doesn't exist yet, see above.)
    mCurrentSection = 0;

    mBackend = std::make_unique<MadBackend>();
    mBackend->setOnReady([this] { onBackendReady(); });
    // Capture-stream input lock: the modal handles its own input (it's
    // window-topmost) but the panel must swallow anything else while locked.
    mBackend->setEventCallback("input.lock", [this](const rapidjson::Value& data) {
        mInputLocked = MadJson::getBool(data, "locked", false);
        // Testers set "nav":true — the tested pad is grabbed, so let other pads
        // still navigate (reach STOP). Captures omit it and stay swallowed.
        mInputLockAllowNav = mInputLocked && MadJson::getBool(data, "nav", false);
    });
    // The backend pushes state.rev whenever config/devices/bezels change; sum
    // the counters into the epoch that gates kept-alive page reuse. Registered
    // before spawn() so no early push is missed (poll() dispatches them).
    mBackend->setEventCallback("state.rev", [this](const rapidjson::Value& data) {
        int epoch {0};
        if (data.IsObject())
            for (auto it = data.MemberBegin(); it != data.MemberEnd(); ++it)
                if (it->value.IsInt())
                    epoch += it->value.GetInt();
        mStateEpoch = epoch;
    });
    showConnecting();
    mBackend->spawn();
}

void GuiMadPanel::onBackendReady()
{
    LOG(LogInfo) << "GuiMadPanel: backend ready (backend stderr -> "
                    "~/Emulation/storage/controller-router/mad-backend.log)";
    // A backend death mid-capture must not leave the panel locked forever.
    mInputLocked = false;
    // The fresh daemon's stream-token counter restarts at s1 and the old
    // subscribers were dropped in shutdownChild() — forget the old watch token
    // so ensureDeviceWatch() re-registers cleanly instead of early-returning
    // on a token match.
    mDeviceWatchToken.clear();
    // A fresh daemon restarts its revision counters at 0 and any kept-alive
    // pages hold data from the dead one — drop them and reset the epoch so every
    // section rebuilds against the new backend.
    for (auto& root : mSavedRoots)
        root.reset();
    mStateEpoch = 0;

    if (!mSidebarBuilt) {
        // Very first Ready ever: DON'T flip to PanelState::Ready and don't touch the
        // (nonexistent) sidebar/page yet. applySidebarVisibility() performs the one-time
        // unconditional first build — sidebar widget + landing page together, already in
        // whatever order sidebar.sections returns — so the first thing ever painted is the
        // saved SIDEBAR_ORDER, not the hardcoded default snapping into place a frame later.
        // The panel stays in Connecting (busy spinner) for this one cheap local RPC.
        requestSidebarVisibility();
        return;
    }

    // Reconnect (backend restart or a Tk-session/RETRY return): the sidebar widget already
    // exists from a previous Ready — keep the original behavior of rebuilding the section the
    // user was on right away, then async-reconciling visibility/order without yanking them
    // off it.
    mPanelState = PanelState::Ready;
    // Re-request on every (re)connect: a backend death before the art.resolve
    // response must not leave the sidebar label-only for the whole session.
    // art.resolve is cheap and idempotent.
    requestSidebarIcons();
    // (Re)build the current section — this also runs after a backend restart
    // following a classic Tk session or a RETRY.
    switchSection(mCurrentSection);
    // Then filter the sidebar to the rows the backend reports visible (capability
    // auto-hide + install.conf overrides). Async, after switchSection so the panel
    // shows immediately; a missing/erroring RPC leaves all rows (release-skew safe).
    requestSidebarVisibility();
}

void GuiMadPanel::showConnecting()
{
    mPanelState = PanelState::Connecting;
    mPageStack.clear();
    mStatusText->setText("Starting MAD backend…");
    // Hard clear (flash included): outside Ready the prompts aren't
    // suppressed, so leftover flash text would overlap them in the strip.
    mFooter->clear();
    updateHelpPrompts();
}

void GuiMadPanel::showError(const std::string& message)
{
    mPanelState = PanelState::Errored;
    mInputLocked = false;
    mPageStack.clear();
    mStatusText->setText(message);
    // The button sits below the (wrapped) error text.
    mRetryButton->setPosition(
        mContentPos.x + (mContentSize.x - mRetryButton->getSize().x) / 2.0f,
        mStatusText->getPosition().y + mStatusText->getSize().y + mContentSize.y * 0.06f);
    // Hard clear (flash included): the centered text already explains the
    // error, and outside Ready leftover footer text would overlap the
    // "A retry / B back" prompts in the strip.
    mFooter->clear();
    updateHelpPrompts();
}

void GuiMadPanel::requestSidebarIcons()
{
    // MAD theme XMLs win first: <icon name="sidebar"> in a page's theme file
    // (or the global one) replaces that section's sidebar icon outright.
    for (size_t i {0}; i < mSections.size(); ++i) {
        const std::string themed {
            MadTheme::pageIconPath(mSections[i].artKey, "sidebar")};
        if (!themed.empty())
            mSidebar->setIcon(static_cast<int>(i), themed);
    }

    // One art.resolve call resolves every sidebar icon through the backend's
    // theme → launchers-art → esde-build-art chain (mirrors the Tk sidebar).
    std::vector<Section> sections {mSections};
    mBackend->request(
        "art.resolve",
        [sections](MadJson::Writer& writer) {
            writer.Key("names");
            writer.StartObject();
            for (const Section& section : sections) {
                writer.Key(section.artKey.c_str(),
                           static_cast<rapidjson::SizeType>(section.artKey.length()));
                writer.StartArray();
                const std::string iconPath {"icons/" + section.artKey + ".png"};
                const std::string flatPath {section.artKey + ".png"};
                writer.String(iconPath.c_str(),
                              static_cast<rapidjson::SizeType>(iconPath.length()));
                writer.String(flatPath.c_str(),
                              static_cast<rapidjson::SizeType>(flatPath.length()));
                if (section.artKey == "x-arcade")
                    writer.String("icons/x-arcade-sidebar.png");
                if (section.artKey == "gamepads")
                    writer.String("icons/genericgamepad.png");
                // Last-resort: the active theme's console.png via the backend's
                // console: resolver (mirrors the Tk sidebar's daphne fallback).
                if (section.artKey == "daphne")
                    writer.String("console:daphne");
                writer.EndArray();
            }
            writer.EndObject();
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok)
                return; // Label-only sidebar.
            const rapidjson::Value& paths {MadJson::getMember(payload, "paths")};
            for (size_t i {0}; i < mSections.size(); ++i) {
                // A MAD-theme sidebar icon outranks the backend art chain.
                if (!MadTheme::pageIconPath(mSections[i].artKey, "sidebar").empty())
                    continue;
                const std::string path {
                    MadJson::getString(paths, mSections[i].artKey.c_str())};
                if (!path.empty())
                    mSidebar->setIcon(static_cast<int>(i), path);
            }
        });
}

void GuiMadPanel::switchSection(const int index)
{
    LOG(LogDebug) << "GuiMadPanel: section -> " << mSections[index].label;
    // Keep the section we're leaving so we can re-show it instantly later.
    stashCurrentRoot();
    mCurrentSection = index;
    MadTheme::getInstance().setActivePage(mSections[index].artKey);
    refreshThemedBackground();
    mSidebar->setActive(index);
    // The sticky status belongs to the old section's pages — don't leak it.
    mFooter->setStatus("");
    mPageStack.clear(); // stashCurrentRoot already moved out the old root

    std::unique_ptr<MadPage>& saved {mSavedRoots[index]};
    if (saved != nullptr && saved->builtEpoch() == mStateEpoch) {
        // Reuse the kept-alive page: no rebuild, no backend request, no loading
        // flash. Re-lay-out (handles a resize) and restore its saved focus.
        MadPage* root {saved.get()};
        mPageStack.emplace_back(std::move(saved));
        root->setPosition(mContentPos.x, mContentPos.y);
        root->setSize(mContentSize.x, mContentSize.y);
        root->onRestoreFocus();
    }
    else {
        // Nothing kept, or its data went stale (epoch advanced) — build fresh.
        saved.reset();
        MadPage* root {makeRootPage(index)};
        root->setTitleHidden(true); // The sidebar already names the section.
        root->setBuiltEpoch(mStateEpoch);
        preparePage(root);
        mPageStack.emplace_back(root);
    }
    updateHelpPrompts();
}

void GuiMadPanel::stashCurrentRoot()
{
    if (mPageStack.empty())
        return;
    // The root is the bottom of the stack; any child pages pushed above it
    // (pickers/details) are transient and dropped on a section switch, as
    // before. Save the root's focus so onRestoreFocus() lands the cursor.
    mPageStack.front()->onSaveFocus();
    mSavedRoots[mCurrentSection] = std::move(mPageStack.front());
    mPageStack.clear(); // releases the moved-from slot + any child pages
}

void GuiMadPanel::requestSidebarVisibility()
{
    mBackend->request(
        "sidebar.sections", [](MadJson::Writer&) {},
        [this](bool ok, const rapidjson::Value& payload) {
            const rapidjson::Value* sections {nullptr};
            if (ok) {
                sections = &MadJson::getMember(payload, "sections");
                if (!sections->IsArray())
                    sections = nullptr;
            }
            if (sections == nullptr) {
                // Fallback: keep ALL rows (e.g. launchers without the RPC yet, or a bad
                // payload). Routed through applySidebarVisibility() with an empty key list:
                // on the very first build (no sidebar widget yet) that falls back to the
                // default mSections order so the panel is never left sidebar-less; on a
                // later (already-built) reconnect it resolves to a same-order no-op.
                applySidebarVisibility({});
                return;
            }
            std::vector<std::string> visible;
            for (rapidjson::SizeType i {0}; i < sections->Size(); ++i)
                if (MadJson::getBool((*sections)[i], "visible", true))
                    visible.emplace_back(MadJson::getString((*sections)[i], "key"));
            applySidebarVisibility(visible);
        });
}

void GuiMadPanel::applySidebarVisibility(const std::vector<std::string>& visibleKeys, bool live)
{
    // Passive (onBackendReady) path: only re-filter on the fresh landing (Preview, no child
    // page pushed, not capture-locked) — UNLESS this is the very first build ever
    // (!mSidebarBuilt), which always proceeds: there's no sidebar/page on screen yet to
    // disturb, and this IS what builds them for the first time. sidebar.sections is async —
    // on a later reconnect, rebuilding after the user has navigated or opened a modal would
    // yank them; it simply applies on the next open. The live (Apply) path manages its own
    // guard inside applySidebarLive.
    if (!live && mSidebarBuilt && (mInputLocked || mCurrentSection != 0 || mPageStack.size() > 1))
        return;

    // Build the visible sections in BACKEND order — this is what makes a saved SIDEBAR_ORDER
    // take effect (sidebar.sections returns rows in that order). Keys not in the catalog are
    // skipped; the all-rows fallback for an absent RPC stays in requestSidebarVisibility.
    std::vector<Section> filtered;
    for (const std::string& key : visibleKeys)
        for (const Section& section : mAllSections)
            if (section.artKey == key) {
                filtered.emplace_back(section);
                break;
            }
    if (filtered.empty()) {
        // Never hide everything. On the first build there's nothing on screen yet to fall
        // back TO — use the default (mSections, still the full hardcoded catalog at this
        // point) order rather than leaving the panel permanently sidebar-less.
        if (!mSidebarBuilt)
            filtered = mSections;
        else
            return;
    }

    // No change (same rows, same order) -> skip the rebuild + its flash. (Live path too: an
    // Apply that doesn't change the visible set or order needs no live rebuild.) Never skipped
    // on the first build: even when the saved order equals the default one, the sidebar
    // widget itself doesn't exist yet and still needs its one-time unconditional build.
    if (mSidebarBuilt && filtered.size() == mSections.size()) {
        bool same {true};
        for (size_t i {0}; i < filtered.size(); ++i)
            if (filtered[i].artKey != mSections[i].artKey) {
                same = false;
                break;
            }
        if (same)
            return;
    }

    if (live) {
        applySidebarLive(filtered);
        return;
    }

    mSections = filtered;
    mPageStack.clear();
    mSavedRoots.clear();
    mSavedRoots.resize(mSections.size());
    rebuildSidebarWidget();
    // First build only: this is where the panel leaves Connecting (the busy spinner) and the
    // sidebar widget starts existing. A no-op on later (already-built) reconciliation passes.
    mPanelState = PanelState::Ready;
    mSidebarBuilt = true;

    mCurrentSection = 0; // land on the (possibly new) first section
    mSidebar->setActive(0);
    requestSidebarIcons();
    switchSection(0);
}

void GuiMadPanel::rebuildSidebarWidget()
{
    std::vector<std::string> labels;
    for (const Section& section : mSections)
        labels.emplace_back(section.label);
    // mSidebar is null on the very first call (no prior widget to remove — see
    // applySidebarVisibility()'s first-build path).
    if (mSidebar != nullptr)
        removeChild(mSidebar.get());
    mSidebar = std::make_unique<MadSidebar>(labels);
    mSidebar->setPosition(0.0f, 0.0f);
    mSidebar->setSize(mSidebarWidth, mSize.y - mHelpReserve);
    addChild(mSidebar.get());
}

void GuiMadPanel::refreshSidebarLive()
{
    mBackend->request(
        "sidebar.sections", [](MadJson::Writer&) {},
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok)
                return;
            const rapidjson::Value& sections {MadJson::getMember(payload, "sections")};
            if (!sections.IsArray())
                return;
            std::vector<std::string> visible;
            for (rapidjson::SizeType i {0}; i < sections.Size(); ++i)
                if (MadJson::getBool(sections[i], "visible", true))
                    visible.emplace_back(MadJson::getString(sections[i], "key"));
            applySidebarVisibility(visible, /*live=*/true);
        });
}

void GuiMadPanel::applySidebarLive(const std::vector<Section>& filtered)
{
    // Re-anchor path for the Sidebar page's Apply. Only if the user is STILL on the Sidebar
    // page (the Apply they pressed) — otherwise the choices are already persisted and apply on
    // the next open; don't yank a user who navigated away before this async reply landed.
    if (mInputLocked || mPageStack.size() != 1 || mSections.empty() ||
        mCurrentSection < 0 || mCurrentSection >= static_cast<int>(mSections.size()) ||
        mSections[mCurrentSection].artKey != "sidebar")
        return;

    // Pages render via mPageStack.back() (not the child system), so re-anchoring is: take
    // ownership of the live Sidebar page across the rebuild, then put it back as the active
    // page at its new index — no rebuild, no free, cursor/scroll preserved.
    mPageStack.front()->onSaveFocus();
    std::unique_ptr<MadPage> live {std::move(mPageStack.front())};
    mPageStack.clear();
    mSavedRoots.clear(); // drop stale cached roots; they rebuild lazily on next visit

    mSections = filtered;
    mSavedRoots.resize(mSections.size());
    rebuildSidebarWidget();

    int newIdx {-1};
    for (size_t i {0}; i < mSections.size(); ++i)
        if (mSections[i].artKey == "sidebar") {
            newIdx = static_cast<int>(i);
            break;
        }
    if (newIdx < 0) { // impossible (sidebar is never hidden) — fail safe to Preview
        live.reset();
        mCurrentSection = 0;
        switchSection(0);
        return;
    }

    mCurrentSection = newIdx;
    mSidebar->setActive(newIdx);
    MadTheme::getInstance().setActivePage("sidebar");
    refreshThemedBackground();
    requestSidebarIcons();

    MadPage* root {live.get()};
    mPageStack.emplace_back(std::move(live));
    root->setPosition(mContentPos.x, mContentPos.y);
    root->setSize(mContentSize.x, mContentSize.y);
    root->onRestoreFocus();
    updateHelpPrompts();
}

MadPage* GuiMadPanel::makeRootPage(const int index)
{
    const Section& section {mSections[index]};
    if (section.label == "Preview")
        return new GuiMadPagePreview(this);
    if (section.label == "Device pins")
        return new GuiMadPagePlayers(this);
    if (section.label == "Quit combo")
        return new GuiMadPageQuitCombo(this);
    if (section.label == "Lightgun")
        return new GuiMadPageLightgun(this);
    if (section.label == "Standalones")
        return new GuiMadPageStandalones(this);
    if (section.label == "RetroArch")
        return new GuiMadPageStandaloneSections(this, GuiMadPageStandaloneSections::Fetch {},
                                                "retroarch.list", "RETROARCH");
    if (section.label == "X-Arcade")
        return new GuiMadPageXArcade(this);
    if (section.label == "Gamepads")
        return new GuiMadPageGamepads(this);
    if (section.label == "Splash")
        return new GuiMadPageSplash(this);
    if (section.label == "Backup")
        return new GuiMadPageBackup(this);
    if (section.label == "Sidebar")
        return new GuiMadPageSidebar(this);
    // Unreachable: every registry entry is mapped above. Fail safe anyway.
    LOG(LogError) << "GuiMadPanel: no page for section \"" << section.label << "\"";
    return new GuiMadPagePreview(this);
}

void GuiMadPanel::ensureDeviceWatch()
{
    // Safe to call on every page build: the backend keeps one watch stream and
    // returns the same token with already:true. After a backend restart the
    // token may change, in which case the callback re-attaches.
    mBackend->request("devices.watch", nullptr,
                      [this](bool ok, const rapidjson::Value& payload) {
                          if (!ok)
                              return;
                          const std::string token {MadJson::getString(payload, "stream")};
                          if (token.empty() || token == mDeviceWatchToken)
                              return;
                          if (!mDeviceWatchToken.empty())
                              mBackend->clearStreamCallback(mDeviceWatchToken);
                          mDeviceWatchToken = token;
                          mBackend->setStreamCallback(
                              token, [this](const rapidjson::Value& data) {
                                  if (MadJson::getBool(data, "closed", false))
                                      return;
                                  if (mPanelState == PanelState::Ready &&
                                      currentPage() != nullptr)
                                      currentPage()->onDevicesChanged(data);
                              });
                      });
}

void GuiMadPanel::preparePage(MadPage* page)
{
    page->setPosition(mContentPos.x, mContentPos.y);
    page->setSize(mContentSize.x, mContentSize.y);
    page->build();
}

void GuiMadPanel::pushPage(MadPage* page)
{
    if (currentPage() != nullptr)
        currentPage()->onSaveFocus();
    // Stickies are page-scoped: the parent's pending status must not cover the
    // child's help prompts. An active flash still finishes — setStatus("")
    // only replaces the saved sticky underneath it.
    mFooter->setStatus("");
    preparePage(page);
    mPageStack.emplace_back(page);
    updateHelpPrompts();
}

void GuiMadPanel::popPage()
{
    if (mPageStack.size() <= 1)
        return;
    // The dying page's sticky must not outlive it: every clear path is
    // page-owned (pageRequest callbacks are dropped, stream callbacks are
    // cleared in destructors), so an orphaned sticky would cover the help
    // prompts until the next section switch.
    mFooter->setStatus("");
    mPageStack.pop_back();
    currentPage()->onRestoreFocus();
    // The popped child may have changed what the revealed page displays (e.g. a
    // per-system edit changes a dot/badge on the page it returns to).
    currentPage()->onChildPopped();
    updateHelpPrompts();
}

void GuiMadPanel::backOut()
{
    NavigationSounds::getInstance().playThemeNavigationSound(BACKSOUND);
    if (mPageStack.size() > 1)
        popPage();
    else
        delete this; // Back to the Utilities menu.
}

void GuiMadPanel::promptUnsavedThen(MadPage* page, const std::function<void()>& proceed)
{
    // The dialog is modal (top of the window stack), so `page` can't be popped or
    // switched out from under us while it's up; the currentPage() checks are belt
    // and suspenders. madSave/madCancel send input_save/input_cancel to the daemon
    // synchronously (the write/revert lands even though `proceed` then destroys the
    // page and drops the response callback). We run `proceed` ONLY when the action
    // reported success: if a page no-ops it (e.g. Lindbergh with a bind in flight),
    // staying keeps the staged edits instead of silently dropping them.
    mWindow->pushGui(new GuiMsgBox(
        "You have unsaved changes.",
        "SAVE",
        [this, page, proceed] {
            if (currentPage() == page && !page->madSave())
                return; // couldn't save right now — stay so the edit isn't lost
            proceed();
        },
        "DISCARD",
        [this, page, proceed] {
            if (currentPage() == page && !page->madCancel())
                return; // couldn't discard right now — stay
            proceed();
        },
        "KEEP EDITING", nullptr));
}

bool GuiMadPanel::input(InputConfig* config, Input input)
{
    // Tk parity, panel-GLOBAL: the keyboard NEVER navigates or activates MAD —
    // the Sinden driver synthesizes display-server keystrokes from gun presses
    // (arrows/Return/Escape would browse, pick, and close). The button-map
    // page still receives the events to light its ● dots.
    if (input.device == DEVICE_KEYBOARD) {
        if (mPanelState == PanelState::Ready && !mInputLocked && currentPage() != nullptr)
            currentPage()->onKeyboardInput(config, input);
        return true;
    }

    if (mPanelState == PanelState::Errored) {
        if (input.value != 0 && config->isMappedTo("b", input)) {
            NavigationSounds::getInstance().playThemeNavigationSound(BACKSOUND);
            delete this;
            return true;
        }
        mRetryButton->input(config, input);
        return true;
    }

    if (mPanelState == PanelState::Connecting) {
        if (input.value != 0 && config->isMappedTo("b", input)) {
            NavigationSounds::getInstance().playThemeNavigationSound(BACKSOUND);
            delete this;
        }
        return true;
    }

    // A CAPTURE stream is live (Daphne/button detect): the press the daemon is
    // reading also reaches SDL; the capture modal handles its own input and the
    // panel must swallow the rest. A TESTER lock sets nav=true (the tested pad
    // is grabbed, no echo), so other pads still navigate — e.g. reach STOP.
    if (mInputLocked && !mInputLockAllowNav)
        return true;

    if (input.value != 0) {
        if (config->isMappedTo("b", input)) {
            // The page may consume B itself (e.g. cancel a reorder carry).
            if (currentPage() != nullptr && currentPage()->onBackPressed())
                return true;
            // A buffered page with staged, unsaved edits: confirm before leaving so
            // the edits are never silently dropped (Save / Discard / Keep editing).
            if (currentPage() != nullptr && currentPage()->hasUnsavedEdits()) {
                promptUnsavedThen(currentPage(), [this] { backOut(); });
                return true;
            }
            backOut();
            return true;
        }
        // While a page locks section-nav (e.g. the X-Arcade tester editing positions), the
        // shoulder/trigger buttons must NOT switch section or scroll — fall through so they
        // reach the page's own input() (which swallows them). B is handled above, untouched.
        if (!(currentPage() != nullptr && currentPage()->consumesSectionNav())) {
            if (config->isMappedLike("leftshoulder", input)) {
                const int target {(mCurrentSection + static_cast<int>(mSections.size()) - 1) %
                                  static_cast<int>(mSections.size())};
                // A section switch also destroys the current page — guard staged edits.
                if (currentPage() != nullptr && currentPage()->hasUnsavedEdits()) {
                    promptUnsavedThen(currentPage(), [this, target] {
                        NavigationSounds::getInstance().playThemeNavigationSound(SYSTEMBROWSESOUND);
                        switchSection(target);
                    });
                    return true;
                }
                NavigationSounds::getInstance().playThemeNavigationSound(SYSTEMBROWSESOUND);
                switchSection(target);
                return true;
            }
            if (config->isMappedLike("rightshoulder", input)) {
                const int target {(mCurrentSection + 1) % static_cast<int>(mSections.size())};
                if (currentPage() != nullptr && currentPage()->hasUnsavedEdits()) {
                    promptUnsavedThen(currentPage(), [this, target] {
                        NavigationSounds::getInstance().playThemeNavigationSound(SYSTEMBROWSESOUND);
                        switchSection(target);
                    });
                    return true;
                }
                NavigationSounds::getInstance().playThemeNavigationSound(SYSTEMBROWSESOUND);
                switchSection(target);
                return true;
            }
            if (config->isMappedLike("lefttrigger", input)) {
                if (currentPage() != nullptr)
                    currentPage()->pageScroll(-1);
                return true;
            }
            if (config->isMappedLike("righttrigger", input)) {
                if (currentPage() != nullptr)
                    currentPage()->pageScroll(1);
                return true;
            }
        }
    }

    if (input.value != 0 && config->isMappedTo("x", input) && currentPage() != nullptr &&
        currentPage()->madSave())
        return true;
    if (input.value != 0 && config->isMappedTo("y", input) && currentPage() != nullptr &&
        currentPage()->madCancel())
        return true;

    if (currentPage() != nullptr)
        currentPage()->input(config, input);

    // Swallow everything else — input must never leak to the ES-DE views below.
    return true;
}

void GuiMadPanel::update(int deltaTime)
{
    // Response/event callbacks only fire from this poll(), i.e. while the panel
    // is topmost (Window only updates the top GUI). The backend's reader thread
    // keeps draining the pipe regardless, so the daemon never blocks on a full
    // pipe even when a GuiMsgBox or similar covers the panel.
    mBackend->poll();

    if (mBackend->state() == MadBackend::State::Errored && mPanelState != PanelState::Errored)
        showError(mBackend->errorMessage());

    if (mPanelState == PanelState::Connecting)
        mBusy.update(deltaTime);

    if (mPanelState == PanelState::Ready && currentPage() != nullptr)
        currentPage()->update(deltaTime);

    GuiComponent::update(deltaTime);
}

void GuiMadPanel::refreshThemedBackground()
{
    const std::string path {MadTheme::backgroundPath()};
    if (path != mBackgroundImagePath) {
        mBackgroundImagePath = path;
        if (!path.empty()) {
            // Crisp pixel-art scaling; stretched edge-to-edge like the
            // reference theme stretches its view backgrounds. FULL height so the
            // footer/help strip inherits the same themed squares + per-page tint
            // as the content (not just the flat frame color).
            mBackgroundImage.setLinearInterpolation(false);
            mBackgroundImage.setResize(mSize.x, mSize.y);
            mBackgroundImage.setImage(path);
            mBackgroundImage.setPosition(0.0f, 0.0f);
            mBackgroundImage.setOrigin(0.0f, 0.0f);
        }
    }
    // The tint can differ per page even when the image is shared.
    if (!mBackgroundImagePath.empty())
        mBackgroundImage.setColorShift(MadTheme::backgroundColor());
}

void GuiMadPanel::render(const glm::mat4& parentTrans)
{
    glm::mat4 trans {parentTrans * getTransform()};
    mRenderer->setMatrix(trans);

    // Fully opaque, flat menu-scheme background — the gamelist view behind the
    // panel must not show through. FULL height now (incl. the help strip) so
    // the strip shows the MAD page color, not the gamelist behind — ES-DE's own
    // help row (drawn by Window BEFORE the top GUI) is covered; we re-render our
    // own help on top below (mStripHelp).
    mRenderer->drawRect(0.0f, 0.0f, mSize.x, mSize.y, MadTheme::color(MadColor::Frame),
                        MadTheme::color(MadColor::Frame));
    // Themed background image (squares + per-page tint) on top of the opaque
    // base, FULL height — so the footer/help strip matches the page, not a flat
    // frame bar. Help/status text renders on top (same as the content area).
    if (!mBackgroundImagePath.empty())
        mBackgroundImage.render(trans);
    // Thin separator between the sidebar and the content area.
    mRenderer->drawRect(mSidebarWidth, 0.0f, std::max(1.0f, mSize.x * 0.0012f),
                        mSize.y - mHelpReserve, MadTheme::color(MadColor::Separators), MadTheme::color(MadColor::Separators));

    renderChildren(trans);

    if (mPanelState == PanelState::Ready && currentPage() != nullptr)
        currentPage()->render(trans);

    if (mPanelState == PanelState::Connecting) {
        mStatusText->render(trans);
        mBusy.render(trans);
    }
    else if (mPanelState == PanelState::Errored) {
        mStatusText->render(trans);
        mRetryButton->render(trans);
    }

    // Our own help row, on the themed strip (the footer's status, drawn in
    // renderChildren above, replaces it when present — getHelpPrompts() returns
    // empty while the footer has text). Re-feed only when the prompts change.
    const std::vector<HelpPrompt> prompts {getHelpPrompts()};
    std::string sig;
    for (const HelpPrompt& p : prompts)
        sig += p.first + "\x1f" + p.second + "\x1e";
    if (sig != mStripHelpSig) {
        mStripHelpSig = sig;
        mStripHelp.setPrompts(prompts);
    }
    if (!prompts.empty())
        mStripHelp.render(trans);
}

std::vector<HelpPrompt> GuiMadPanel::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;

    // The footer owns the help strip while it has something to say.
    if (mPanelState == PanelState::Ready && mFooter != nullptr && mFooter->hasText())
        return prompts;

    if (mPanelState == PanelState::Errored) {
        prompts.push_back(HelpPrompt("a", "retry"));
        prompts.push_back(HelpPrompt("b", "back"));
        return prompts;
    }
    if (mPanelState == PanelState::Connecting) {
        prompts.push_back(HelpPrompt("b", "back"));
        return prompts;
    }

    if (currentPage() != nullptr)
        prompts = currentPage()->getHelpPrompts();
    prompts.push_back(HelpPrompt("lr", "section"));
    prompts.push_back(HelpPrompt("b", mPageStack.size() > 1 ? "back" : "close"));
    return prompts;
}
