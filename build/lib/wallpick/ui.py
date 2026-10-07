"""GTK4 / libadwaita front-end for wallpick."""

from __future__ import annotations

import concurrent.futures as futures
import shutil
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import api, config, setter  # noqa: E402

CSS = b"""
.wp-tile { padding: 0; border-radius: 10px; }
.wp-badge {
  background-color: rgba(0, 0, 0, 0.62);
  color: #ffffff;
  font-size: 0.72em;
  padding: 1px 6px;
  border-radius: 6px;
  margin: 5px;
}
.wp-badge.warn { background-color: rgba(160, 60, 40, 0.85); }
.meta-key { font-size: 0.82em; opacity: 0.6; }
.tag-chip {
  font-size: 0.82em;
  padding: 2px 8px;
  border-radius: 999px;
  background-color: alpha(currentColor, 0.09);
}
.welcome-icon { opacity: 0.7; }
"""


# --------------------------------------------------------------------------
# Widgets
# --------------------------------------------------------------------------


class Tile(Gtk.Button):
    """One wallpaper in the grid."""

    def __init__(self, wallpaper: dict, width: int) -> None:
        super().__init__()
        self.wallpaper = wallpaper
        self.set_has_frame(False)
        self.add_css_class("wp-tile")
        self.set_tooltip_text(wallpaper.get("resolution", ""))

        height = max(90, int(width * 0.62))
        self.picture = Gtk.Picture()
        self.picture.set_content_fit(Gtk.ContentFit.COVER)
        self.picture.set_can_shrink(True)
        self.picture.set_size_request(width, height)

        overlay = Gtk.Overlay()
        overlay.set_child(self.picture)

        resolution = Gtk.Label(label=wallpaper.get("resolution", ""))
        resolution.add_css_class("wp-badge")
        resolution.set_halign(Gtk.Align.END)
        resolution.set_valign(Gtk.Align.END)
        overlay.add_overlay(resolution)

        purity = (wallpaper.get("purity") or "sfw").lower()
        if purity != "sfw":
            flag = Gtk.Label(label=purity.upper())
            flag.add_css_class("wp-badge")
            flag.add_css_class("warn")
            flag.set_halign(Gtk.Align.START)
            flag.set_valign(Gtk.Align.START)
            overlay.add_overlay(flag)

        self.set_child(overlay)

    def set_bytes(self, data: bytes) -> None:
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except GLib.Error:
            return
        self.picture.set_paintable(texture)


class NameDialog(Gtk.Window):
    """Asks for the filename to save a wallpaper under."""

    def __init__(self, parent: Gtk.Window, default_name: str, extension: str,
                 directory: Path, on_save) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_title("Download wallpaper")
        self.set_default_size(440, -1)
        self.set_resizable(False)
        self._on_save = on_save
        self._extension = extension
        self._directory = directory

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)

        self.entry = Gtk.Entry()
        self.entry.set_text(default_name)
        self.entry.set_activates_default(True)
        self.entry.select_region(0, len(default_name))
        box.append(_labelled("File name", self.entry))

        folder_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.folder_label = Gtk.Label(label=str(directory))
        self.folder_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
        self.folder_label.set_xalign(0.0)
        self.folder_label.set_hexpand(True)
        self.folder_label.add_css_class("dim-label")
        folder_row.append(self.folder_label)
        browse = Gtk.Button(label="Change\u2026")
        browse.connect("clicked", self._on_browse)
        folder_row.append(browse)
        box.append(_labelled("Folder", folder_row))

        self.hint = Gtk.Label(label=f"Saved as \u201c{default_name}{extension}\u201d")
        self.hint.set_xalign(0.0)
        self.hint.add_css_class("meta-key")
        self.entry.connect("changed", self._on_changed)
        box.append(self.hint)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        buttons.set_margin_top(6)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._on_confirm)
        buttons.append(cancel)
        buttons.append(save)
        box.append(buttons)

        self.set_child(box)
        self.set_default_widget(save)

    def _on_changed(self, *_args) -> None:
        stem = self.entry.get_text().strip() or "wallpaper"
        self.hint.set_label(f"Saved as \u201c{self._final_name(stem)}\u201d")

    def _final_name(self, stem: str) -> str:
        stem = stem.strip().replace("/", "-")
        if Path(stem).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            return stem
        return stem + self._extension

    def _on_browse(self, *_args) -> None:
        if not hasattr(Gtk, "FileDialog"):
            return
        dialog = Gtk.FileDialog()
        dialog.set_title("Download folder")
        try:
            dialog.set_initial_folder(Gio.File.new_for_path(str(self._directory)))
        except GLib.Error:
            pass

        def done(source, result):
            try:
                folder = source.select_folder_finish(result)
            except GLib.Error:
                return
            if folder and folder.get_path():
                self._directory = Path(folder.get_path())
                self.folder_label.set_label(str(self._directory))

        dialog.select_folder(self, None, done)

    def _on_confirm(self, *_args) -> None:
        stem = self.entry.get_text().strip() or "wallpaper"
        target = self._directory / self._final_name(stem)
        self.close()
        self._on_save(target)


class ApiKeyDialog(Gtk.Window):
    """First-run dialog that asks for the Wallhaven API key."""

    def __init__(self, parent: Gtk.Window, on_done) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_title("Welcome to wallpick")
        self.set_default_size(500, -1)
        self.set_resizable(False)
        self._on_done = on_done

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

        # Icon / welcome
        icon = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
        icon.set_pixel_size(64)
        icon.add_css_class("welcome-icon")
        icon.set_halign(Gtk.Align.CENTER)
        box.append(icon)

        title = Gtk.Label(label="Welcome to wallpick!")
        title.add_css_class("title-1")
        title.set_halign(Gtk.Align.CENTER)
        box.append(title)

        description = Gtk.Label(
            label=(
                "Browse and apply wallpapers from Wallhaven.\n\n"
                "SFW wallpapers work without an API key.\n"
                "To access Sketchy and NSFW content, enter your "
                "Wallhaven API key below. You can find it at:\n"
                "https://wallhaven.cc/settings/account"
            )
        )
        description.set_wrap(True)
        description.set_xalign(0.0)
        description.set_justify(Gtk.Justification.LEFT)
        box.append(description)

        self.key_entry = Gtk.PasswordEntry()
        self.key_entry.set_show_peek_icon(True)
        self.key_entry.set_placeholder_text("Paste your Wallhaven API key (optional)")
        self.key_entry.set_activates_default(True)
        box.append(_labelled("API Key", self.key_entry))

        hint = Gtk.Label(
            label="You can always change this later in the app menu."
        )
        hint.set_xalign(0.0)
        hint.add_css_class("meta-key")
        box.append(hint)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        buttons.set_margin_top(8)

        skip = Gtk.Button(label="Skip")
        skip.connect("clicked", self._on_skip)
        buttons.append(skip)

        save = Gtk.Button(label="Save & Continue")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._on_save)
        buttons.append(save)

        box.append(buttons)
        self.set_child(box)
        self.set_default_widget(save)

    def _on_skip(self, *_args) -> None:
        self.close()
        self._on_done("")

    def _on_save(self, *_args) -> None:
        key = self.key_entry.get_text().strip()
        self.close()
        self._on_done(key)


def _labelled(caption: str, child: Gtk.Widget) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    label = Gtk.Label(label=caption)
    label.set_xalign(0.0)
    label.add_css_class("meta-key")
    box.append(label)
    box.append(child)
    return box


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------


class WallpickWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, cfg: config.Config) -> None:
        super().__init__(application=application, title="wallpick")
        self.cfg = cfg
        self.client = api.Wallhaven(cfg["api_key"])
        self.pool = futures.ThreadPoolExecutor(
            max_workers=max(1, int(cfg["concurrent_thumbs"]))
        )

        self.token = 0
        self.page = 1
        self.last_page = 1
        self.loading = False
        self.detail_wallpaper: dict = {}
        self.busy = False

        self.set_default_size(1180, 780)

        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)
        self.stack.add_named(self._build_grid_page(), "grid")
        self.stack.add_named(self._build_detail_page(), "detail")
        self.toasts.set_child(self.stack)
        self.set_content(self.toasts)

        self._setup_shortcuts()

        self.connect("close-request", self._on_close)

        # Show first-run dialog or load wallpapers immediately
        if cfg.get("first_run", True):
            GLib.idle_add(self._show_first_run_dialog)
        else:
            self.load_page(reset=True)

    # --- shortcuts -----------------------------------------------------------

    def _setup_shortcuts(self) -> None:
        """Register keyboard shortcuts via Gio actions."""
        shortcuts = [
            ("search-focus", ["<Control>f"], self._action_focus_search),
            ("refresh", ["<Control>r", "F5"], self._action_refresh),
            ("quit", ["<Control>q"], self._action_quit),
            ("go-back", ["Escape"], self._action_go_back),
            ("toggle-dark", ["<Control>d"], self._action_toggle_dark),
            ("apply-wallpaper", ["<Control>a"], self._action_apply),
            ("download-wallpaper", ["<Control>s"], self._action_download),
            ("open-web", ["<Control>w"], self._action_open_web),
            ("set-api-key", ["<Control>k"], self._action_set_api_key),
        ]

        app = self.get_application()
        for name, accels, callback in shortcuts:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            app.add_action(action)
            app.set_accels_for_action(f"app.{name}", accels)

    def _action_focus_search(self, *_args) -> None:
        if self.stack.get_visible_child_name() == "grid":
            self.search_entry.grab_focus()

    def _action_refresh(self, *_args) -> None:
        if self.stack.get_visible_child_name() == "grid":
            self.load_page(reset=True)

    def _action_quit(self, *_args) -> None:
        self.close()

    def _action_go_back(self, *_args) -> None:
        if self.stack.get_visible_child_name() == "detail":
            self.show_grid()

    def _action_toggle_dark(self, *_args) -> None:
        self._toggle_dark_theme()

    def _action_apply(self, *_args) -> None:
        if self.stack.get_visible_child_name() == "detail":
            self._on_apply()

    def _action_download(self, *_args) -> None:
        if self.stack.get_visible_child_name() == "detail":
            self._on_download()

    def _action_open_web(self, *_args) -> None:
        if self.stack.get_visible_child_name() == "detail":
            self._on_open_web()

    def _action_set_api_key(self, *_args) -> None:
        self._show_api_key_dialog()

    # --- first-run setup -----------------------------------------------------

    def _show_first_run_dialog(self) -> bool:
        dialog = ApiKeyDialog(self, self._on_first_run_done)
        dialog.present()
        return False

    def _on_first_run_done(self, api_key: str) -> None:
        if api_key:
            self.cfg["api_key"] = api_key
            self.client.set_api_key(api_key)
        self.cfg["first_run"] = False
        self.cfg.save()
        self.load_page(reset=True)

    def _show_api_key_dialog(self) -> None:
        """Show the API key dialog from the menu (not first-run)."""
        dialog = ApiKeyDialog(self, self._on_api_key_updated)
        dialog.present()

    def _on_api_key_updated(self, api_key: str) -> None:
        self.cfg["api_key"] = api_key
        self.client.set_api_key(api_key)
        self.cfg.save()
        if api_key:
            self.toast("API key saved.")
        else:
            self.toast("API key cleared. NSFW content is disabled.")

    # --- dark-theme toggle ---------------------------------------------------

    def _toggle_dark_theme(self) -> None:
        style = Adw.StyleManager.get_default()
        if style.get_dark():
            style.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
            self.cfg["dark_theme"] = False
            self.dark_button.set_icon_name("weather-clear-night-symbolic")
            self.dark_button.set_tooltip_text("Switch to dark theme (Ctrl+D)")
        else:
            style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            self.cfg["dark_theme"] = True
            self.dark_button.set_icon_name("display-brightness-symbolic")
            self.dark_button.set_tooltip_text("Switch to light theme (Ctrl+D)")
        self.cfg.save()

    def _apply_theme(self) -> None:
        """Apply saved theme preference on startup."""
        style = Adw.StyleManager.get_default()
        if self.cfg.get("dark_theme", True):
            style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            style.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

    # --- construction --------------------------------------------------------

    def _build_grid_page(self) -> Gtk.Widget:
        header = Adw.HeaderBar()

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search wallhaven\u2026")
        self.search_entry.set_hexpand(True)
        self.search_entry.set_size_request(320, -1)
        self.search_entry.connect("activate", lambda *_: self.load_page(reset=True))
        header.set_title_widget(self.search_entry)

        filters = Gtk.MenuButton()
        filters.set_icon_name("view-more-symbolic")
        filters.set_tooltip_text("Filters")
        filters.set_popover(self._build_filter_popover())
        header.pack_start(filters)

        # App menu (hamburger)
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_tooltip_text("Menu")
        menu_button.set_menu_model(self._build_app_menu())
        header.pack_start(menu_button)

        sortings = [label for _, label in api.SORTINGS]
        self.sort_drop = Gtk.DropDown.new_from_strings(sortings)
        current_sorting = self.cfg["sorting"]
        for index, (value, _) in enumerate(api.SORTINGS):
            if value == current_sorting:
                self.sort_drop.set_selected(index)
                break
        self.sort_drop.connect("notify::selected", self._on_sorting_changed)
        header.pack_end(self.sort_drop)

        self.nsfw_toggle = Gtk.ToggleButton(label="NSFW")
        self.nsfw_toggle.set_active(bool(self.cfg["nsfw"]))
        self.nsfw_toggle.connect("toggled", self._on_purity_toggled, "nsfw")
        header.pack_end(self.nsfw_toggle)

        self.sketchy_toggle = Gtk.ToggleButton(label="Sketchy")
        self.sketchy_toggle.set_active(bool(self.cfg["sketchy"]))
        self.sketchy_toggle.connect("toggled", self._on_purity_toggled, "sketchy")
        header.pack_end(self.sketchy_toggle)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_tooltip_text("Reload (Ctrl+R)")
        refresh.connect("clicked", lambda *_: self.load_page(reset=True))
        header.pack_end(refresh)

        # Dark theme toggle button
        is_dark = self.cfg.get("dark_theme", True)
        self.dark_button = Gtk.Button()
        if is_dark:
            self.dark_button.set_icon_name("display-brightness-symbolic")
            self.dark_button.set_tooltip_text("Switch to light theme (Ctrl+D)")
        else:
            self.dark_button.set_icon_name("weather-clear-night-symbolic")
            self.dark_button.set_tooltip_text("Switch to dark theme (Ctrl+D)")
        self.dark_button.connect("clicked", lambda *_: self._toggle_dark_theme())
        header.pack_end(self.dark_button)

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flowbox.set_homogeneous(True)
        self.flowbox.set_column_spacing(10)
        self.flowbox.set_row_spacing(10)
        self.flowbox.set_min_children_per_line(2)
        self.flowbox.set_max_children_per_line(8)
        self.flowbox.set_margin_top(10)
        self.flowbox.set_margin_bottom(10)
        self.flowbox.set_margin_start(10)
        self.flowbox.set_margin_end(10)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_vexpand(True)
        self.scroller.set_child(self.flowbox)
        self.scroller.get_vadjustment().connect("value-changed", self._on_scrolled)

        self.status = Adw.StatusPage()
        self.status.set_icon_name("image-missing-symbolic")
        self.status.set_title("Nothing here")
        self.status.set_vexpand(True)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(28, 28)
        self.spinner.set_halign(Gtk.Align.CENTER)
        self.spinner.set_valign(Gtk.Align.CENTER)

        self.grid_stack = Gtk.Stack()
        self.grid_stack.add_named(self.scroller, "results")
        self.grid_stack.add_named(self.status, "empty")
        self.grid_stack.add_named(self.spinner, "loading")

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self.grid_stack)
        return view

    def _build_filter_popover(self) -> Gtk.Popover:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_size_request(240, -1)

        categories = self.cfg["categories"].ljust(3, "0")
        self.cat_checks = []
        cat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for index, name in enumerate(("General", "Anime", "People")):
            check = Gtk.CheckButton(label=name)
            check.set_active(categories[index] == "1")
            check.connect("toggled", lambda *_: None)
            self.cat_checks.append(check)
            cat_box.append(check)
        box.append(_labelled("Categories", cat_box))

        self.atleast_entry = Gtk.Entry()
        self.atleast_entry.set_text(self.cfg["atleast"])
        self.atleast_entry.set_placeholder_text("1920x1080")
        box.append(_labelled("Minimum resolution", self.atleast_entry))

        self.ratios_entry = Gtk.Entry()
        self.ratios_entry.set_text(self.cfg["ratios"])
        self.ratios_entry.set_placeholder_text("16x9,16x10")
        box.append(_labelled("Aspect ratios", self.ratios_entry))

        apply_button = Gtk.Button(label="Apply filters")
        apply_button.add_css_class("suggested-action")
        apply_button.connect("clicked", self._on_filters_applied)
        box.append(apply_button)

        popover = Gtk.Popover()
        popover.set_child(box)
        self.filter_popover = popover
        return popover

    def _build_app_menu(self) -> Gio.Menu:
        """Build the hamburger menu model."""
        menu = Gio.Menu()
        menu.append("Set API Key\tCtrl+K", "app.set-api-key")
        menu.append("Keyboard Shortcuts", "app.show-shortcuts")
        return menu

    def _build_detail_page(self) -> Gtk.Widget:
        header = Adw.HeaderBar()
        back = Gtk.Button(icon_name="go-previous-symbolic")
        back.set_tooltip_text("Back (Escape)")
        back.connect("clicked", lambda *_: self.show_grid())
        header.pack_start(back)

        self.detail_title = Adw.WindowTitle()
        header.set_title_widget(self.detail_title)

        self.detail_picture = Gtk.Picture()
        self.detail_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.detail_picture.set_can_shrink(True)
        self.detail_picture.set_vexpand(True)
        self.detail_picture.set_margin_top(10)
        self.detail_picture.set_margin_start(10)
        self.detail_picture.set_margin_end(10)

        self.detail_meta = Gtk.Label()
        self.detail_meta.set_xalign(0.0)
        self.detail_meta.add_css_class("meta-key")
        self.detail_meta.set_ellipsize(3)

        self.tag_box = Gtk.FlowBox()
        self.tag_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.tag_box.set_max_children_per_line(12)
        self.tag_box.set_column_spacing(6)
        self.tag_box.set_row_spacing(4)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        info.set_hexpand(True)
        info.append(self.detail_meta)
        info.append(self.tag_box)

        self.detail_spinner = Gtk.Spinner()
        self.apply_button = Gtk.Button(label="Apply")
        self.apply_button.set_tooltip_text("Apply as wallpaper (Ctrl+A)")
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.connect("clicked", self._on_apply)
        self.download_button = Gtk.Button(label="Download\u2026")
        self.download_button.set_tooltip_text("Download wallpaper (Ctrl+S)")
        self.download_button.connect("clicked", self._on_download)
        self.open_button = Gtk.Button(icon_name="web-browser-symbolic")
        self.open_button.set_tooltip_text("Open on wallhaven.cc (Ctrl+W)")
        self.open_button.connect("clicked", self._on_open_web)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_valign(Gtk.Align.CENTER)
        actions.append(self.detail_spinner)
        actions.append(self.open_button)
        actions.append(self.download_button)
        actions.append(self.apply_button)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bottom.set_margin_top(8)
        bottom.set_margin_bottom(10)
        bottom.set_margin_start(12)
        bottom.set_margin_end(12)
        bottom.append(info)
        bottom.append(actions)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.detail_picture)
        body.append(bottom)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(body)
        return view

    # --- search / paging -----------------------------------------------------

    def _purity(self) -> str:
        return api.purity_bits(
            self.sketchy_toggle.get_active(), self.nsfw_toggle.get_active()
        )

    def _sorting(self) -> str:
        return api.SORTINGS[self.sort_drop.get_selected()][0]

    def load_page(self, reset: bool = False) -> None:
        if self.loading:
            return
        if reset:
            self.token += 1
            self.page = 1
            self.last_page = 1
            self._clear_grid()
            self.grid_stack.set_visible_child_name("loading")
            self.spinner.start()

        token = self.token
        self.loading = True

        query = self.search_entry.get_text()
        categories = "".join("1" if c.get_active() else "0" for c in self.cat_checks)
        if categories == "000":
            categories = "111"

        params = dict(
            query=query,
            page=self.page,
            categories=categories,
            purity=self._purity(),
            sorting=self._sorting(),
            order=self.cfg["order"],
            top_range=self.cfg["top_range"],
            atleast=self.atleast_entry.get_text(),
            ratios=self.ratios_entry.get_text(),
        )

        def work() -> None:
            try:
                items, meta = self.client.search(**params)
            except api.WallhavenError as exc:
                GLib.idle_add(self._on_search_failed, token, str(exc))
                return
            GLib.idle_add(self._on_results, token, items, meta)

        threading.Thread(target=work, daemon=True).start()

    def _on_search_failed(self, token: int, message: str) -> bool:
        if token != self.token:
            return False
        self.loading = False
        self.spinner.stop()
        if self._tile_count() == 0:
            self.status.set_title("Could not load wallpapers")
            self.status.set_description(message)
            self.grid_stack.set_visible_child_name("empty")
        self.toast(message)
        return False

    def _on_results(self, token: int, items: list[dict], meta: dict) -> bool:
        if token != self.token:
            return False
        self.loading = False
        self.spinner.stop()
        self.last_page = int(meta.get("last_page") or 1)

        if not items and self._tile_count() == 0:
            self.status.set_title("No wallpapers matched")
            self.status.set_description("Try a different search or loosen the filters.")
            self.grid_stack.set_visible_child_name("empty")
            return False

        width = int(self.cfg["thumb_width"])
        for wallpaper in items:
            tile = Tile(wallpaper, width)
            tile.connect("clicked", self._on_tile_clicked)
            self.flowbox.append(tile)
            self.pool.submit(self._fetch_thumb, token, tile, wallpaper)

        self.grid_stack.set_visible_child_name("results")
        return False

    def _fetch_thumb(self, token: int, tile: Tile, wallpaper: dict) -> None:
        if token != self.token:
            return
        thumbs = wallpaper.get("thumbs") or {}
        url = thumbs.get("small") or thumbs.get("original") or thumbs.get("large")
        if not url:
            return
        cached = config.THUMB_DIR / f"{wallpaper.get('id', 'unknown')}.jpg"
        data = b""
        try:
            if cached.is_file() and cached.stat().st_size > 0:
                data = cached.read_bytes()
            else:
                data = self.client.fetch_bytes(url)
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(data)
        except (api.WallhavenError, OSError):
            return
        if data and token == self.token:
            GLib.idle_add(self._set_tile_bytes, token, tile, data)

    def _set_tile_bytes(self, token: int, tile: Tile, data: bytes) -> bool:
        if token == self.token:
            tile.set_bytes(data)
        return False

    def _on_scrolled(self, adjustment: Gtk.Adjustment) -> None:
        if self.loading or self.page >= self.last_page:
            return
        near_end = (
            adjustment.get_value() + adjustment.get_page_size()
            >= adjustment.get_upper() - 400
        )
        if near_end:
            self.page += 1
            self.load_page(reset=False)

    def _clear_grid(self) -> None:
        child = self.flowbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.flowbox.remove(child)
            child = nxt

    def _tile_count(self) -> int:
        count = 0
        child = self.flowbox.get_first_child()
        while child is not None:
            count += 1
            child = child.get_next_sibling()
        return count

    # --- header callbacks -----------------------------------------------------

    def _on_sorting_changed(self, *_args) -> None:
        self.cfg["sorting"] = self._sorting()
        self.cfg.save()
        self.load_page(reset=True)

    def _on_purity_toggled(self, button: Gtk.ToggleButton, which: str) -> None:
        if which == "nsfw" and button.get_active() and not self.client.has_key:
            button.set_active(False)
            self.toast("NSFW needs a Wallhaven API key. Set one via Ctrl+K or the menu.")
            return
        self.cfg[which] = button.get_active()
        self.cfg.save()
        self.load_page(reset=True)

    def _on_filters_applied(self, *_args) -> None:
        self.cfg["categories"] = "".join(
            "1" if c.get_active() else "0" for c in self.cat_checks
        )
        self.cfg["atleast"] = self.atleast_entry.get_text().strip()
        self.cfg["ratios"] = self.ratios_entry.get_text().strip()
        self.cfg.save()
        self.filter_popover.popdown()
        self.load_page(reset=True)

    # --- detail view ----------------------------------------------------------

    def _on_tile_clicked(self, tile: Tile) -> None:
        self.show_detail(tile.wallpaper)

    def show_grid(self) -> None:
        self.stack.set_visible_child_name("grid")

    def show_detail(self, wallpaper: dict) -> None:
        self.detail_wallpaper = wallpaper
        wid = wallpaper.get("id", "")
        self.detail_title.set_title(wid)
        self.detail_title.set_subtitle(wallpaper.get("resolution", ""))
        self.detail_picture.set_paintable(None)
        self._clear_tags()

        size_mb = (wallpaper.get("file_size") or 0) / (1024 * 1024)
        self.detail_meta.set_label(
            "  \u00b7  ".join(
                part
                for part in (
                    wallpaper.get("resolution", ""),
                    f"{size_mb:.1f} MB" if size_mb else "",
                    (wallpaper.get("category") or "").title(),
                    (wallpaper.get("purity") or "").upper(),
                    f"{wallpaper.get('views', 0)} views",
                )
                if part
            )
        )
        self.stack.set_visible_child_name("detail")

        thumbs = wallpaper.get("thumbs") or {}
        preview = thumbs.get("original") or thumbs.get("large") or thumbs.get("small")
        if preview:
            threading.Thread(
                target=self._load_preview, args=(wid, preview), daemon=True
            ).start()
        if wid:
            threading.Thread(target=self._load_tags, args=(wid,), daemon=True).start()

    def _load_preview(self, wid: str, url: str) -> None:
        try:
            data = self.client.fetch_bytes(url, timeout=30)
        except api.WallhavenError:
            return
        GLib.idle_add(self._set_preview, wid, data)

    def _set_preview(self, wid: str, data: bytes) -> bool:
        if self.detail_wallpaper.get("id") != wid:
            return False
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except GLib.Error:
            return False
        self.detail_picture.set_paintable(texture)
        return False

    def _load_tags(self, wid: str) -> None:
        try:
            detail = self.client.wallpaper(wid)
        except api.WallhavenError:
            return
        GLib.idle_add(self._set_tags, wid, list(api.flatten_tags(detail)))

    def _set_tags(self, wid: str, tags: list[str]) -> bool:
        if self.detail_wallpaper.get("id") != wid:
            return False
        self._clear_tags()
        for tag in tags[:14]:
            chip = Gtk.Label(label=tag)
            chip.add_css_class("tag-chip")
            self.tag_box.append(chip)
        return False

    def _clear_tags(self) -> None:
        child = self.tag_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.tag_box.remove(child)
            child = nxt

    # --- actions --------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.apply_button.set_sensitive(not busy)
        self.download_button.set_sensitive(not busy)
        if busy:
            self.detail_spinner.start()
        else:
            self.detail_spinner.stop()

    def _cached_full(self, wallpaper: dict) -> Path:
        wid = wallpaper.get("id", "wallpaper")
        return config.FULL_DIR / f"{wid}{api.extension_for(wallpaper)}"

    def _ensure_full(self, wallpaper: dict) -> Path:
        target = self._cached_full(wallpaper)
        if target.is_file() and target.stat().st_size > 0:
            return target
        url = wallpaper.get("path")
        if not url:
            raise api.WallhavenError("This wallpaper has no download URL")
        return self.client.download(url, target)

    def _on_apply(self, *_args) -> None:
        wallpaper = self.detail_wallpaper
        if not wallpaper or self.busy:
            return
        self._set_busy(True)
        self.toast("Fetching full image\u2026")

        def work() -> None:
            try:
                path = self._ensure_full(wallpaper)
                backend = setter.apply(path, self.cfg["setter_command"])
            except (api.WallhavenError, setter.SetterError, OSError) as exc:
                GLib.idle_add(self._finish_action, str(exc))
                return
            GLib.idle_add(self._finish_action, f"Wallpaper applied via {backend}")

        threading.Thread(target=work, daemon=True).start()

    def _on_download(self, *_args) -> None:
        wallpaper = self.detail_wallpaper
        if not wallpaper or self.busy:
            return
        directory = self.cfg.download_dir
        default_name = f"wallhaven-{wallpaper.get('id', 'wallpaper')}"
        dialog = NameDialog(
            self,
            default_name,
            api.extension_for(wallpaper),
            directory,
            lambda target: self._save_as(wallpaper, target),
        )
        dialog.present()

    def _save_as(self, wallpaper: dict, target: Path) -> None:
        self._set_busy(True)
        self.toast("Downloading\u2026")

        def work() -> None:
            try:
                source = self._ensure_full(wallpaper)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            except (api.WallhavenError, OSError) as exc:
                GLib.idle_add(self._finish_action, str(exc))
                return
            GLib.idle_add(self._finish_action, f"Saved to {target}", str(target.parent))

        threading.Thread(target=work, daemon=True).start()

    def _finish_action(self, message: str, remember_dir: str = "") -> bool:
        self._set_busy(False)
        if remember_dir and remember_dir != self.cfg["download_dir"]:
            self.cfg["download_dir"] = remember_dir
            self.cfg.save()
        self.toast(message)
        return False

    def _on_open_web(self, *_args) -> None:
        url = self.detail_wallpaper.get("url")
        if not url:
            return
        Gtk.UriLauncher.new(url).launch(self, None, None)

    # --- misc -----------------------------------------------------------------

    def toast(self, message: str) -> None:
        toast = Adw.Toast.new(message)
        toast.set_timeout(4)
        self.toasts.add_toast(toast)

    def _on_close(self, *_args) -> bool:
        self.token += 1
        self.pool.shutdown(wait=False, cancel_futures=True)
        return False


# --------------------------------------------------------------------------
# Shortcuts window
# --------------------------------------------------------------------------

def _build_shortcuts_window() -> Gtk.ShortcutsWindow:
    """Build a GTK ShortcutsWindow showing all keyboard shortcuts."""
    window = Gtk.ShortcutsWindow()

    section = Gtk.ShortcutsSection(title="Shortcuts", visible=True)
    section.set_property("section-name", "shortcuts")

    # Navigation group
    nav_group = Gtk.ShortcutsGroup(title="Navigation", visible=True)
    _add_shortcut(nav_group, "<Control>f", "Focus search bar")
    _add_shortcut(nav_group, "<Control>r", "Reload / Refresh")
    _add_shortcut(nav_group, "F5", "Reload / Refresh")
    _add_shortcut(nav_group, "Escape", "Go back to grid")
    _add_shortcut(nav_group, "<Control>q", "Quit")
    section.append(nav_group)

    # Actions group
    action_group = Gtk.ShortcutsGroup(title="Actions", visible=True)
    _add_shortcut(action_group, "<Control>a", "Apply wallpaper")
    _add_shortcut(action_group, "<Control>s", "Download wallpaper")
    _add_shortcut(action_group, "<Control>w", "Open on wallhaven.cc")
    section.append(action_group)

    # Settings group
    settings_group = Gtk.ShortcutsGroup(title="Settings", visible=True)
    _add_shortcut(settings_group, "<Control>d", "Toggle dark / light theme")
    _add_shortcut(settings_group, "<Control>k", "Set API key")
    section.append(settings_group)

    window.append(section)
    return window


def _add_shortcut(group: Gtk.ShortcutsGroup, accel: str, title: str) -> None:
    shortcut = Gtk.ShortcutsShortcut(
        accelerator=accel,
        title=title,
        visible=True,
    )
    group.append(shortcut)


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------


class WallpickApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=config.APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )
        self.cfg = config.Config()

    def do_activate(self) -> None:  # noqa: N802 (GObject naming)
        window = self.props.active_window
        if not window:
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
            window = WallpickWindow(self, self.cfg)
            window._apply_theme()

            # Register the shortcuts-window action
            show_shortcuts = Gio.SimpleAction.new("show-shortcuts", None)
            show_shortcuts.connect("activate", self._on_show_shortcuts)
            self.add_action(show_shortcuts)

        window.present()

    def _on_show_shortcuts(self, *_args) -> None:
        win = _build_shortcuts_window()
        win.set_transient_for(self.props.active_window)
        win.present()


def main(argv: list[str] | None = None) -> int:
    config.ensure_dirs()
    return WallpickApp().run(argv if argv is not None else sys.argv)
