// A native macOS alert showing text in a fixed-width font, scrollable and selectable.
//   osascript -l JavaScript srcp_popup.js TITLE TEXT BUTTON [BUTTON ...]
// Prints the index of the button pressed (0 = first). With SRCP_POPUP_DRYRUN=1 it builds
// the alert, prints its size and exits without showing it (for tests).
ObjC.import("Cocoa");

function run(argv) {
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

  const scroll = $.NSScrollView.alloc.initWithFrame($.NSMakeRect(0, 0, width, height));
  // Both scrollers, shown only when needed: long lines scroll sideways instead of wrapping
  // (wrapped lines would make the height estimate wrong and clip the end).
  scroll.hasVerticalScroller = true;
  scroll.hasHorizontalScroller = true;
  scroll.autohidesScrollers = true;
  scroll.borderType = $.NSNoBorder;
  scroll.drawsBackground = false;

  const view = $.NSTextView.alloc.initWithFrame($.NSMakeRect(0, 0, width, height));
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

  const alert = $.NSAlert.alloc.init;
  alert.messageText = title;
  alert.informativeText = "";
  alert.accessoryView = scroll;
  for (const b of buttons) alert.addButtonWithTitle(b);
  // Escape (and Cmd-.) only map to a button titled "Cancel"; give it to the first (Done).
  if (buttons.length > 0) alert.buttons.objectAtIndex(0).keyEquivalent = "\u001b";

  if ($.NSProcessInfo.processInfo.environment.objectForKey("SRCP_POPUP_DRYRUN").js === "1") {
    return `dryrun ${width}x${height} lines=${lines.length} buttons=${buttons.length}`;
  }
  app.activateIgnoringOtherApps(true);
  return String(alert.runModal - 1000);
}
