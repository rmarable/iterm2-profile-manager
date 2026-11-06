// Fixed-width fonts as macOS sees them, for .tools/srcp_fonts.py (osascript -l JavaScript).
//   scan             -> JSON list of [PostScript name, family, file path, weight 0-15, italic]
//   resolve NAME...  -> the first NAME macOS can open as a font, or "" if none
//   faces PATH...    -> JSON list of the PostScript names of every face in those font files
ObjC.import("AppKit");
ObjC.import("CoreText");
ObjC.bindFunction("CTFontCopyAttribute", ["id", ["id", "id"]]);
ObjC.bindFunction("CTFontManagerCreateFontDescriptorsFromURL", ["id", ["id"]]);
ObjC.bindFunction("CTFontDescriptorCopyAttribute", ["id", ["id", "id"]]);

function run(argv) {
  if (argv[0] === "resolve") {
    for (var i = 1; i < argv.length; i++) {
      var font = $.NSFont.fontWithNameSize(argv[i], 12);
      if (!font.isNil()) return argv[i];
    }
    return "";
  }
  if (argv[0] === "faces") {
    var names = [];
    argv.slice(1).forEach(function (p) {
      var descs = $.CTFontManagerCreateFontDescriptorsFromURL($.NSURL.fileURLWithPath(p));
      if (!descs || descs.isNil()) return;
      for (var i = 0; i < descs.count; i++) {
        var name = $.CTFontDescriptorCopyAttribute(descs.objectAtIndex(i), $("NSFontNameAttribute"));
        if (name && !name.isNil()) names.push(name.js);
      }
    });
    return JSON.stringify(names);
  }
  var fm = $.NSFontManager.sharedFontManager;
  var names = fm.availableFontNamesWithTraits($.NSFixedPitchFontMask).js;
  var out = [];
  names.forEach(function (n) {
    var ps = n.js;
    var f = $.NSFont.fontWithNameSize(ps, 12);
    if (f.isNil()) return;
    var url = $.CTFontCopyAttribute(f, $("NSCTFontFileURLAttribute"));
    var path = url && !url.isNil() && url.path ? url.path.js : "";
    var italic = (fm.traitsOfFont(f) & $.NSItalicFontMask) !== 0;
    out.push([ps, f.familyName.js, path, Number(fm.weightOfFont(f)), italic]);
  });
  return JSON.stringify(out);
}
