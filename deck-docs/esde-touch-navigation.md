# ES-DE touch navigation (fork feature, deck-patches TOUCH)

Tap-the-UI touchscreen navigation for the ES-DE-MAD fork. Built 2026-07-29. Upstream
ES-DE has NO touch or mouse support in the UI on Linux and explicitly rejected tap
navigation (FAQ-ANDROID.md, checked 2026-07-29: "ES-DE is not a good match for gesture
navigation"); their only touch mechanism is the Android/iOS virtual-gamepad overlay
whose sources (InputOverlay.cpp/.h) are NOT shipped in the public tree. This feature is
therefore fork-only and must be reapplied on every upstream rebase.

## Facts verified on this Deck (2026-07-29)

- Game Mode gamescope session runs STEAM_TOUCH_CLICK_MODE = 4 (passthrough): apps get
  NATIVE touch events (xprop -root on display :0). gamescope --help lists
  --default-touch-mode 0..4 (hover/left/right/middle/passthrough).
- SDL2 synthesizes mouse events from touches by default (SDL_HINT_TOUCH_MOUSE_EVENTS=1,
  which == SDL_TOUCH_MOUSEID). ES-DE sets no touch/mouse SDL hints anywhere.
- Upstream DEVICE_TOUCH plumbing (InputConfig id -2, GUID "-3", loadTouchConfig() with
  14 mapped actions) ships in the tree behind Android/iOS guards and works on Linux
  once un-guarded; only InputOverlay references must be avoided.

## Architecture (all edits marked `deck-patches TOUCH`)

- es-core/src/TouchNavigation.{h,cpp} (NEW): gesture recognizer singleton. Consumes
  native finger events (primary) plus real-mouse events (Desktop Mode; SDL_TOUCH_MOUSEID
  duplicates filtered). Gestures: tap (slop 1.5% of short screen edge, min 8 px), drag,
  fling (kinetic, half-life 0.35 s, start 350 px/s, stop 60 px/s), two-finger tap = b.
  10 s staleness recovery + reset() from the main.cpp stale-input flush. Rotation remap
  for ScreenRotate 0/90/180/270 (only 0 verified on-device).
- GuiComponent gains `pointerInput(PointerEvent, parentTrans)` (recursive dispatch,
  topmost child first) + `pointerWithinBounds()`. SCROLL events hit-test with the
  gesture START point so drags stay with the surface they started on (stateless
  capture, no dangling pointers by construction).
- Window gains pointerPress/pointerTap/pointerScroll: press wakes screensaver or
  dismisses the launch screen (gesture swallowed); tap order is media/PDF viewer
  zones (left/right thirds = prev/next, center = b = close), then the GUI stack,
  then the help bar as a FALLBACK for unconsumed taps (every prompt is a tappable
  button, composite lr/ltrt split by half; fallback order matters: a full-screen
  GUI like the MAD panel covers the bar, which may sit at a THEMED position, and
  the covered bar must never hijack content taps), then backdrop-tap = b when a
  menu is open. The MAD panel hit-tests its own visible mStripHelp and synthesizes
  the tapped prompt, so tapping "close"/"back" in the MAD footer behaves exactly
  like the B button (backOut: pop one drilled level, close at a section root).
  Fling ticks from Window::update().
- Input synthesis: TouchNavigation::synthesizeInput(name) resolves via
  getInputByName on the DEVICE_TOUCH config (so InputSwapButtons is honored) and sends
  press+release through Window::input(). CONTRACT: components synthesize as their LAST
  action and return immediately (the input may delete the caller).
- Per-surface overrides: ComponentList (tap = select+activate, cursor-row tap offers
  the row elements first for slider stepping; drag/fling row-stepped), ComponentGrid
  (focus follows tap), ButtonComponent, SliderComponent (tap-side step),
  NinePatchComponent (dialog interior dead zone), HelpComponent (prompt hit rects),
  TextListComponent (tap-select / tap-again-activate), CarouselComponent (tap center =
  a, tap side item = jump N, axis drag; wheels use thirds), ViewController/SystemView
  forwarding (ViewController also drops gestures/flings when a camera transition
  starts), MadSidebar (tap entry = guarded switchSection, drag scrolls),
  MadScrollView (drag scrolls the view; taps recurse into children under the scroll
  offset), MadVirtualList (tap/drag), MadTileGrid (tap tile = cursor there, tap
  cursor tile = pick; drags fall through to the scrollview when embedded),
  MadStepper (tap arrows = step, tap value box = picker), MadChipRow (tap chip =
  toggle), MadReorderList (tap = select, tap selected = lift/drop, while carrying
  drag one row per row-height or tap the destination; fires setOnPointerChanged so
  the 5 owning pages run followFocus + refreshHelpPrompts), GuiMadPagePreview
  (self-scrolling body: drag handled by a page override), GuiMadPanel (mirrors
  input()'s state/lock gates incl. a tappable RETRY when Errored, then children,
  then manual page dispatch, consumes panel bounds; switchSection resets
  TouchNavigation so flings can't cross sections). GamelistView and the on-screen
  keyboard need no code (default recursion). Known cosmetic limit: a tap-activated
  widget fires its callback directly, so the page's focus FRAME may stay on the
  previously focused widget until the next D-pad move.

## Round 2 (2026-07-29): MAD page items + reorder

MadTileGrid/MadStepper/MadChipRow taps; MadScrollView forwards taps to children;
GuiMadPagePreview drag (self-scrolling body); MadReorderList touch reorder: tap =
select (adopts page focus via setOnFocusRequested -> setFocusTarget, so a lift can
NEVER happen on an unfocused list), tap selected = lift/drop, while carrying drag
one row per row-height or tap the destination row. CARRY-OWNS-THE-PAGE invariant
(from the round-2 review): while carrying, MadScrollView routes every event to the
carrying list only and MadPage::pointerInput (base override) guards taps outside
the scroll view - any tap that misses the list DROPS the carried row in place
(dropCarry commits the on-screen order) so Save/Apply/chips can never act on a
half-moved order. Registration via MadPage::setTouchCarry(scroll, list) with
weak_ptrs (rebuild-safe); wired in the 5 reorder pages (Sidebar, PriorityEdit,
PadsPriority, RAControllers, PergamePads). PointerEvent gained a `fling` flag
(set by TouchNavigation::update) - a carried row ignores fling inertia so drops
are finger-precise. MadTileGrid drag steps skip (not clamp) past-the-end rows so
a vertical drag never drifts the cursor column on a short last row. The footer
"close"/"back" prompt is tappable via the panel's own mStripHelp hit-test
(synthesizes b -> backOut: pop one level / close at root); Window's help-bar
hit-test moved AFTER the GUI-stack dispatch (fallback, not override) so the
covered, possibly theme-positioned bar can't hijack content taps under MAD.

## Review (2026-07-29, heavy fleet, 32 agents)

Confirmed and fixed before first compile: CRITICAL template two-phase-lookup break
in CarouselComponent::pointerInput (List:: qualification); fling firing from a stale
velocity sample after drag-stop-hold-release (release velocity now computed from the
final sample window); slider tap-side threshold ignoring the real knob geometry
(mKnob width + mBarLength); viewer taps hijacked by the stale invisible help bar
(viewer zones now checked first); MAD panel pointer path missing the Errored /
Connecting / input-lock gates and the RETRY button; missed two-finger taps degrading
into activating taps; parseEvent touch cases now Linux-only (#elif) so Windows/macOS
stay byte-identical; ComponentList taps in the render-clipped bottom strip are dead;
pointer mapping subtracts the renderer viewport origin (ScreenOffset/padding).

## Settings

- `InputTouchNavigation` (bool, default false): the "ENABLE TOUCH" switch in Input
  Device Settings (above IGNORE KEYBOARD INPUT). Applies live via setCallback.
- `DebugTouchNavigation` (bool, default false, no menu row): per-gesture LogInfo
  lines in es_log.txt (raw + mapped coords, tap/drag/fling/synth events).

## Upstream-rebase hotspots (re-apply recipe)

Everything is additive blocks EXCEPT four small inline edits in
es-core/src/InputManager.cpp: TOUCH_GUID_STRING hoisted out of the platform guards,
and `|| defined(__linux__)` added to the guards at mTouchInputConfig creation,
getDeviceGUIDString, getInputConfigByDevice and the loadTouchConfig body (trigger ids
use local TOUCH_NAV_TRIGGER_LEFT/RIGHT 100/101 on non-Android). parseEvent gains an
`#else` branch (finger + mouse cases) on the Android finger block. main.cpp's fork
stale-input flush also flushes finger/mouse events + TouchNavigation::reset().
getNumConfiguredDevices stays Android-only ON PURPOSE (device counts unchanged).

## Known v1 limitations

- GridComponent themes, MAD tile grids/steppers/reorder lists, slider drag-to-value,
  description-text drag scroll, keyboard caret placement, media-viewer swipe: phase 2.
- ScreenRotate 90/180/270 remap implemented but unverified on-device.
- DateTimeEditComponent editing still needs a controller once entered.

## Phase 2 (shipped 2026-07-30, fork commit 6cbd139dc)
Facts confirmed while building slider drag / description drag / carousel 1:1 /
caret tap / Players taps (source: the fork's own code, verified by build + on-screen):
- There is NO gesture-release event. Components that need "gesture ended" use an
  idle timer in update() (carousel settles after 150 ms of no SCROLL, fling ticks
  included, so a fling settles only after decaying). A RELEASE event was evaluated
  twice and rejected: flings keep arriving AFTER the physical release.
- Per-gesture latches (ComponentList's owner shared_ptr, SliderComponent's axis
  claim) must be cleared on every non-touch interaction (tap, pad input, focus
  loss) - the firstEvent reset alone leaks state across a mid-gesture GUI pop.
- TextEditComponent disables shaping, so TextComponent's glyphPositions table is
  per CODEPOINT (unicodeLength+1 entries, entry k = caret after glyph k-1, .y =
  line top). Convert a table index to a BYTE offset (Utils::String::moveCursor)
  before setCursor - passing a codepoint index corrupts UTF-8.
- ScrollableContainer's auto-scroll reset paths (end-fade, media-viewer branch,
  allow-text-scrolling) all fight a manual position; a park flag checked FIRST in
  update() is the only reliable way to hold a spot until resetComponent().
- ComponentList's clipped bottom overflow strip must be a dead zone for BOTH taps
  and drag-offers (an invisible row's slider is reachable otherwise).
