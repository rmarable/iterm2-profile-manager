# Monospace font inventory

<!-- Generated: python3 .tools/srcp_fonts.py --catalog > docs/MONOSPACE-FONT-INVENTORY.md
     (a test checks it matches; edit the tool, not this file). -->

The fixed-width fonts iTerm2 Profile Manager knows about, where each comes from, and how to
add one to the fonts it picks at random for new tabs.

## See the fonts on your Mac

```sh
python3 .tools/srcp_fonts.py
```

writes a page showing every font below side by side in the same sample text, and opens it in
your browser. Each one says whether it's in the pool, whether it's installed on your Mac, and
where it comes from; Homebrew ones you don't have yet are previewed from a web copy, where
there is one. Run it again any time to see the current state. `--no-open` and `--out PATH`
are in `--help`.

## Add or remove a font

```sh
python3 .tools/srcp_fonts.py --add "JetBrains Mono"
python3 .tools/srcp_fonts.py --remove "JetBrains Mono"
```

Homebrew fonts aren't installed by default. The one exception is MesloLGS Nerd Font:
`setup.sh` installs it with Homebrew, if Homebrew is there, because Oh My Zsh prompts need it.
`--add` installs a Homebrew font for you, then adds it to the pool; a font already on your Mac
is just added. `--help` lists every font below. Details: [REFERENCE.md](REFERENCE.md#fonts-tool).

## Catalog

Homebrew casks and PostScript names checked 2026-11-06. Names come from each cask's own font
files; for variable fonts `--add` tries each name listed until macOS knows one.

| Font | Source | In the default pool | PostScript name | Add it |
|---|---|---|---|---|
| SF Mono | Terminal.app (copied by setup.sh) | yes | `SFMono-Regular` |  |
| SF Mono Terminal | Terminal.app (copied by setup.sh) | yes | `SFMonoTerminal-Regular` |  |
| Menlo | macOS system | yes | `Menlo-Regular` |  |
| Monaco | macOS system | yes | `Monaco` |  |
| PT Mono | macOS system | yes | `PTMono-Regular` |  |
| Andale Mono | macOS system | yes | `AndaleMono` |  |
| MesloLGS Nerd Font Mono | Homebrew: font-meslo-lg-nerd-font | yes | `MesloLGSNFM-Regular` |  |
| Courier New | macOS system |  | `CourierNewPSMT` | `--add "Courier New"` |
| MesloLGM Nerd Font Mono | Homebrew: font-meslo-lg-nerd-font |  | `MesloLGMNFM-Regular` | `--add "MesloLGM Nerd Font Mono"` |
| MesloLGL Nerd Font Mono | Homebrew: font-meslo-lg-nerd-font |  | `MesloLGLNFM-Regular` | `--add "MesloLGL Nerd Font Mono"` |
| MesloLGSDZ Nerd Font Mono | Homebrew: font-meslo-lg-nerd-font |  | `MesloLGSDZNFM-Regular` | `--add "MesloLGSDZ Nerd Font Mono"` |
| MesloLGMDZ Nerd Font Mono | Homebrew: font-meslo-lg-nerd-font |  | `MesloLGMDZNFM-Regular` | `--add "MesloLGMDZ Nerd Font Mono"` |
| MesloLGLDZ Nerd Font Mono | Homebrew: font-meslo-lg-nerd-font |  | `MesloLGLDZNFM-Regular` | `--add "MesloLGLDZ Nerd Font Mono"` |
| 0xProto | Homebrew: font-0xproto |  | `0xProto-Regular` | `--add "0xProto"` |
| Cascadia Code | Homebrew: font-cascadia-code |  | `CascadiaCode-Regular` or `CascadiaCodeRoman-Regular` | `--add "Cascadia Code"` |
| Commit Mono | Homebrew: font-commit-mono |  | `CommitMono-Regular` | `--add "Commit Mono"` |
| DejaVu Sans Mono | Homebrew: font-dejavu |  | `DejaVuSansMono` | `--add "DejaVu Sans Mono"` |
| Fira Code | Homebrew: font-fira-code |  | `FiraCode-Regular` | `--add "Fira Code"` |
| Geist Mono | Homebrew: font-geist-mono |  | `GeistMono-Regular` | `--add "Geist Mono"` |
| Hack | Homebrew: font-hack |  | `Hack-Regular` | `--add "Hack"` |
| IBM Plex Mono | Homebrew: font-ibm-plex-mono |  | `IBMPlexMono` | `--add "IBM Plex Mono"` |
| Inconsolata | Homebrew: font-inconsolata |  | `Inconsolata-Regular` | `--add "Inconsolata"` |
| Intel One Mono | Homebrew: font-intel-one-mono |  | `IntelOneMono-Regular` | `--add "Intel One Mono"` |
| Iosevka Term | Homebrew: font-iosevka |  | `Iosevka-Term` | `--add "Iosevka Term"` |
| JetBrains Mono | Homebrew: font-jetbrains-mono |  | `JetBrainsMono-Regular` | `--add "JetBrains Mono"` |
| Maple Mono | Homebrew: font-maple-mono |  | `MapleMono-Regular` | `--add "Maple Mono"` |
| Monaspace Neon | Homebrew: font-monaspace |  | `MonaspaceNeon-Regular` | `--add "Monaspace Neon"` |
| Roboto Mono | Homebrew: font-roboto-mono |  | `RobotoMono-Regular` | `--add "Roboto Mono"` |
| Source Code Pro | Homebrew: font-source-code-pro |  | `SourceCodePro-Regular` | `--add "Source Code Pro"` |
| Ubuntu Mono | Homebrew: font-ubuntu-mono |  | `UbuntuMono-Regular` | `--add "Ubuntu Mono"` |
| Victor Mono | Homebrew: font-victor-mono |  | `VictorMono-Regular` | `--add "Victor Mono"` |
