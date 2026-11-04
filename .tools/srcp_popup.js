// A native macOS alert showing text in a fixed-width font, scrollable and selectable,
// optionally with a dropdown to choose one name (srcp_never_show.py).
//   osascript -l JavaScript srcp_popup.js [--choose "NAME\nNAME..."] TITLE TEXT BUTTON [BUTTON ...]
// Prints the index of the button pressed (0 = first), and with --choose a tab and the index
// of the chosen name. Run from .tools/srcp_popup.py. With SRCP_POPUP_DRYRUN=1 it builds the
// alert, prints its size and exits without showing it (for tests).
ObjC.import("Cocoa");

// NSAlert gives Escape only to a button titled "Cancel" (a hidden one ignores keys, and a
// button has one key, so Done can't take Escape without losing Return). So Escape, Return
// and Enter end the alert here, as if Done (the first button) was clicked. The text view
// has the keyboard when the alert opens and takes keys as keyDown (it swallowed the first
// Return); once it lets go, keys reach the scroll view as key equivalents.
const CLOSE_KEYS = ["\u001b", "\r", "\u0003"];
const closeOn = (event) => {
  if (!CLOSE_KEYS.includes(event.charactersIgnoringModifiers.js)) return false;
  $.NSApp.stopModalWithCode(1000);
  return true;
};
ObjC.registerSubclass({
  name: "SRCPPopupTextView",
  superclass: "NSTextView",
  methods: {
    "keyDown:": {
      types: ["void", ["id"]],
      implementation: function (event) {
        if (!closeOn(event)) ObjC.super(this).keyDown(event);
      },
    },
  },
});
ObjC.registerSubclass({
  name: "SRCPPopupScrollView",
  superclass: "NSScrollView",
  methods: {
    "performKeyEquivalent:": {
      types: ["bool", ["id"]],
      implementation: closeOn,
    },
  },
});

function run(argv) {
  let choices = null;
  if (argv[0] === "--choose") {
    choices = (argv[1] || "").split("\n").filter((n) => n !== "");
    argv = argv.slice(2);
  }
  const [title = "", text = "", ...buttons] = argv;
  const app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyAccessory); // no Dock icon

  // Menlo, not SF Mono: SF Mono has no ★, and a borrowed glyph would break the columns.
  const font = $.NSFont.fontWithNameSize("Menlo-Regular", 12);
  const lines = text.split("\n");
  const charW = font.maximumAdvancement.width;
  const lineH = font.ascender - font.descender + font.leading + 2;
  const longest = Math.max(...lines.map((l) => l.length), 30);
  const width = Math.min(Math.ceil(longest * charW) + 24, 1000);
  const height = Math.min(Math.ceil(lines.length * lineH) + 16, 460);

  const scroll = $.SRCPPopupScrollView.alloc.initWithFrame($.NSMakeRect(0, 0, width, height));
  // Both scrollers, shown only when needed: long lines scroll sideways instead of wrapping
  // (wrapped lines would make the height estimate wrong and clip the end).
  scroll.hasVerticalScroller = true;
  scroll.hasHorizontalScroller = true;
  scroll.autohidesScrollers = true;
  scroll.borderType = $.NSNoBorder;
  scroll.drawsBackground = false;

  const view = $.SRCPPopupTextView.alloc.initWithFrame($.NSMakeRect(0, 0, width, height));
  view.string = text;
  view.font = font;
  view.editable = false;
  view.selectable = true;
  view.drawsBackground = false;
  view.textColor = $.NSColor.labelColor;
  view.verticallyResizable = true;
  view.horizontallyResizable = true;
  view.maxSize = $.NSMakeSize(1e7, 1e7);
  view.textContainer.widthTracksTextView = false;
  view.textContainer.containerSize = $.NSMakeSize(1e7, 1e7);
  view.textContainerInset = $.NSMakeSize(4, 6);
  scroll.documentView = view;

  // The dropdown sits above the text. It refuses the keyboard, so Return and Escape still
  // reach the text view and close the alert as Done; it's chosen by clicking.
  let accessory = scroll;
  let menu = null;
  if (choices && choices.length) {
    const menuH = 26;
    const longestChoice = Math.max(...choices.map((n) => n.length));
    const menuW = Math.min(Math.max(Math.ceil(longestChoice * charW) + 48, 240), 520);
    menu = $.NSPopUpButton.alloc.initWithFramePullsDown(
      $.NSMakeRect(0, height + 6, menuW, menuH),
      false,
    );
    // addItemWithTitle merges equal titles, so the answer is read back by title, not index.
    for (const name of choices) menu.addItemWithTitle(name);
    menu.refusesFirstResponder = true;
    const accessoryW = Math.max(width, menuW);
    accessory = $.NSView.alloc.initWithFrame($.NSMakeRect(0, 0, accessoryW, height + 6 + menuH));
    accessory.addSubview(scroll);
    accessory.addSubview(menu);
  }

  const alert = $.NSAlert.alloc.init;
  alert.messageText = title;
  alert.informativeText = "";
  alert.accessoryView = accessory;
  for (const b of buttons) alert.addButtonWithTitle(b);
  alert.layout;
  alert.window.initialFirstResponder = view;

  const env = $.NSProcessInfo.processInfo.environment.objectForKey("SRCP_POPUP_DRYRUN");
  if (env.js === "1") {
    const extra = menu ? ` choices=${menu.numberOfItems}` : "";
    return `dryrun ${width}x${height} lines=${lines.length} buttons=${buttons.length}${extra}`;
  }
  app.activateIgnoringOtherApps(true);
  const pressed = String(alert.runModal - 1000);
  if (!menu) return pressed;
  return `${pressed}\t${choices.indexOf(menu.titleOfSelectedItem.js)}`;
}
