# Linux Setup

## Preventing Observer Window Focus Stealing

On Linux, the XMage observer windows steal focus from your active window when
they launch. The fix depends on your desktop environment.

### KDE Plasma (Wayland or X11)

Add a KWin window rule that sets "Focus Stealing Prevention: Extreme" for Java
Swing windows:

```bash
cat > ~/.config/kwinrulesrc << 'EOF'
[General]
count=1

[1]
Description=Prevent XMage/Java focus stealing
fsplevel=4
fsplevelrule=2
wmclass=java-lang-Thread
wmclassmatch=1
wmclasscomplete=false
EOF

# Reload KWin rules
dbus-send --session --dest=org.kde.KWin --type=method_call /KWin org.kde.KWin.reconfigure
```

If you already have rules in `kwinrulesrc`, merge manually: increment `count`
in `[General]` and add a new numbered section.

You can also configure this through System Settings > Window Management >
Window Rules > Add New > set "Window class" to `java-lang-Thread`
(substring match), then add "Focus stealing prevention: Force / Extreme".

### GNOME

GNOME's Mutter compositor has a built-in focus-stealing prevention setting:

```bash
gsettings set org.gnome.desktop.wm.preferences focus-new-windows 'strict'
```

### Other WMs (i3, sway, Hyprland, etc.)

Add a `no_focus` rule for Java windows in your WM config. For example in i3:

```
no_focus [class="sun-awt-X11-XFramePeer"]
```

## Java-Side Mitigations

The observer client (`Mage.Client.Observer`) already applies several
focus-prevention measures:

- `setFocusableWindowState(false)` - marks the window as non-focusable
- `setAutoRequestFocus(false)` - prevents internal focus requests
- `toFront()` overridden as no-op - prevents Swing from raising the window
- `Window.Type.UTILITY` - hints to the WM that this is a utility window
- `setExtendedState(MAXIMIZED_BOTH)` suppressed - prevents WM maximize
  (which triggers focus) and uses explicit bounds instead

These help on some WMs but are not sufficient on their own for KDE or GNOME,
which require the compositor-level rules above.
