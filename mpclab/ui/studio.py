"""Focused production workspace; each editor keeps one persistent Qt owner."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QStackedWidget,
    QComboBox,
    QMenu,
    QScrollArea,
    QFrame,
)

from .arrangement_tools import (
    SONG_TEMPLATES,
    enter_song_mode,
    fit_song,
    map_full_song,
    place_sample_selection,
    structure_summary,
)
from .visual_assets import WorkspaceVisuals
from . import theme


def mount():
    widget = QWidget()
    widget.setMinimumWidth(0)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    return widget


def horizontal_strip(widget: QWidget, height: int) -> QScrollArea:
    """Keep dense production chrome usable without imposing a window minimum."""
    widget.adjustSize()
    widget.setMinimumWidth(widget.sizeHint().width())
    scroll = QScrollArea()
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setMinimumWidth(0)
    scroll.setFixedHeight(height)
    scroll.setWidget(widget)
    return scroll


class StudioPanel(QWidget):
    """Reference-inspired shell around the already-wired production editors.

    This deliberately avoids the failed runtime ReferenceConsole approach. The
    mature editor pages still have one owner and use the same holder/dock cycle
    that the recovered build already validated.
    """

    def __init__(self, tabs, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setObjectName("productionStudio")
        self.tabs = tabs
        self.pages, self.holders, self.docks = {}, {}, {}
        self.selected = 2
        self.enabled = False
        self._layout_applied = False
        self._master_view = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # One navigation row; editors always occupy the full central canvas.
        navigation = QWidget()
        navigation.setObjectName("studioModeBar")
        nav = QHBoxLayout(navigation)
        nav.setContentsMargins(8, 3, 8, 3)
        nav.setSpacing(3)
        self.buttons = {}
        for index, title in (
            (2, "Song"),
            (1, "Beats"),
            (6, "Notes"),
            (0, "Sampler"),
            (4, "Instruments"),
            (5, "Autotune"),
            (3, "Mix"),
        ):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setObjectName("workspaceTab")
            button.setMinimumHeight(38)
            button.setMinimumWidth(76)
            button.setMaximumWidth(170)
            button.clicked.connect(lambda checked=False, i=index: self._workspace_clicked(i))
            nav.addWidget(button)
            self.buttons[index] = button

        nav.addStretch(1)
        self.mode_scroll = horizontal_strip(navigation, 46)
        layout.addWidget(self.mode_scroll)

        # Secondary destinations are tools rather than another row of duplicate
        # top-level DAW tabs.
        self.sample_button = self.buttons[0]
        self.instrument_button = self.buttons[4]

        self.more_button = QPushButton("Tools")
        self.more_button.setObjectName("workspaceTool")
        self.more_button.setMinimumHeight(30)
        menu = QMenu(self.more_button)
        menu.addAction("Record vocals in Song", self._record_vocals)
        menu.addAction("Automation", lambda: self._workspace_clicked(7))
        menu.addAction("Master output", self._master_clicked)
        menu.addAction("Musical typing", self._toggle_typing)
        self.song_tools_action = menu.addAction("Song / beat tools")
        self.song_tools_action.setCheckable(True)
        self.song_tools_action.toggled.connect(self._show_actions)
        tips = menu.addAction("Workflow tips")
        tips.setCheckable(True)
        tips.toggled.connect(lambda visible: self.hint.setVisible(visible))
        self.more_button.setMenu(menu)
        nav.addWidget(self.more_button)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setContentsMargins(12, 6, 12, 6)
        self.hint.setObjectName("studioHint")
        self.hint.hide()
        layout.addWidget(self.hint)

        self.beat_tools = QWidget()
        row = QHBoxLayout(self.beat_tools)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("KIT"))
        self.kit = QComboBox()
        self.kit.addItems(["Pocket · hip-hop", "Circuit · house", "Midnight · trap"])
        row.addWidget(self.kit)
        self.create_beat = QPushButton("+ NEW GROOVE")
        self.create_beat.setObjectName("go")
        self.create_beat.setToolTip(
            "Create an editable beat in empty pads; keep existing patterns and sounds"
        )
        self.create_beat.setMinimumHeight(30)
        row.addWidget(self.create_beat)
        row.addStretch()

        self.arrange_tools = QWidget()
        arrange = QHBoxLayout(self.arrange_tools)
        arrange.setContentsMargins(0, 0, 0, 0)
        arrange.setSpacing(6)
        arrange.addWidget(QLabel("SONG MAP"))
        self.song_template = QComboBox()
        for name in SONG_TEMPLATES:
            self.song_template.addItem(name, name)
        self.song_template.setMinimumWidth(150)
        self.song_template.currentTextChanged.connect(self._refresh_song_summary)
        arrange.addWidget(self.song_template)
        self.map_song = QPushButton("MAP FULL SONG")
        self.map_song.setObjectName("go")
        self.map_song.setMinimumHeight(30)
        self.map_song.setToolTip(
            "Create editable Intro/Hook/Verse/Outro pattern sections after the current song"
        )
        self.map_song.clicked.connect(self._map_full_song)
        arrange.addWidget(self.map_song)
        self.sample_to_arrange = QPushButton("SELECTION → ARRANGE")
        self.sample_to_arrange.setMinimumHeight(30)
        self.sample_to_arrange.setToolTip(
            "Place the exact selected sample range on an Arrange audio lane"
        )
        self.sample_to_arrange.clicked.connect(self._sample_to_arrange)
        arrange.addWidget(self.sample_to_arrange)
        self.fit_arrange = QPushButton("FIT SONG")
        self.fit_arrange.setMinimumHeight(30)
        self.fit_arrange.setToolTip("Fit the full current arrangement into the visible timeline")
        self.fit_arrange.clicked.connect(self._fit_arrange)
        arrange.addWidget(self.fit_arrange)
        self.song_summary = QLabel()
        self.song_summary.setObjectName("hint")
        self.song_summary.setMinimumWidth(220)
        arrange.addWidget(self.song_summary)
        self._refresh_song_summary()

        self.action_bar = QWidget()
        self.action_bar.setObjectName("studioActionBar")
        actions = QHBoxLayout(self.action_bar)
        actions.setContentsMargins(10, 4, 10, 4)
        actions.setSpacing(7)
        actions.addWidget(self.beat_tools)
        actions.addWidget(self.arrange_tools)
        actions.addStretch(1)
        self.arrange_pattern = QPushButton("PATTERN → SONG")
        self.arrange_pattern.setMinimumHeight(30)
        self.arrange_pattern.setObjectName("go2")
        self.arrange_pattern.setToolTip(
            "Append the current pattern without replacing existing song clips"
        )
        actions.addWidget(self.arrange_pattern)

        self.action_scroll = horizontal_strip(self.action_bar, 50)
        self.action_scroll.setObjectName("studioActionScroll")
        layout.addWidget(self.action_scroll)

        # Same stable ownership model as the recovered build: no delayed or
        # runtime extraction of live editor widgets from the Qt hierarchy.
        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(0)
        layout.addWidget(self.stack, 1)
        for index in range(8):
            page, title = tabs.widget(index), tabs.tabText(index)
            tabs.removeTab(index)
            holder = mount()
            holder.layout().addWidget(page)
            tabs.insertTab(index, holder, title)
            dock = mount()
            self.stack.addWidget(dock)
            self.pages[index], self.holders[index], self.docks[index] = page, holder, dock
        self.arrangement = self.docks[2]
        self.channels = self.docks[1]
        self.console = self.docks[3]

        self.select(2)
        self.visuals = WorkspaceVisuals(self, self.more_button)
        layout.removeWidget(self.visuals.header)
        layout.insertWidget(1, self.visuals.header)
        self.visuals.header.hide()
        self._apply_reference_style()

    def _show_actions(self, visible):
        self.action_scroll.setVisible(visible and self.selected in (1, 2, 6))

    def _apply_reference_style(self):
        """Every surface and selection follows the user's top-right color control."""
        c = theme.C
        self.setStyleSheet(
            f"""
            #productionStudio {{ background: {c["bg"]}; }}
            #studioModeBar, #studioWorkflowBar, #studioActionBar {{
                background: {c["bg2"]};
                border-bottom: 1px solid {c["line"]};
            }}
            QPushButton#workspaceTab {{
                background: transparent;
                color: {c["dim"]};
                border: 0;
                border-bottom: 2px solid transparent;
                border-radius: 0;
                padding: 5px 9px;
                font-weight: 600;
            }}
            QPushButton#workspaceTab:hover {{ background: {c["hover"]}; color: {c["fg"]}; }}
            QPushButton#workspaceTab:checked {{
                color: {c["accent"]};
                background: {c["item_sel"]};
                border-bottom: 2px solid {c["accent"]};
            }}
            QPushButton#workspaceTool {{
                background: {c["bg2"]};
                color: {c["dim"]};
                border: 1px solid {c["line"]};
                border-radius: 5px;
                padding: 4px 10px;
            }}
            QPushButton#workspaceTool:hover, QPushButton#workspaceTool:checked {{
                color: {c["accent"]};
                border-color: {c["accent"]};
                background: {c["item_sel"]};
            }}
            #studioHint {{ background: {c["bg2"]}; color: {c["dim"]}; }}
            #studioFlow {{ color: {c["dim"]}; padding-right: 8px; }}
            """
        )
        navigation = self.mode_scroll.widget()
        navigation.setMinimumWidth(navigation.sizeHint().width())

    def _app(self):
        candidate = self.tabs.window()
        return (
            candidate if hasattr(candidate, "project") and hasattr(candidate, "playlist") else None
        )

    def _toggle_typing(self):
        app = self._app()
        if app is not None:
            app.toggle_typing_keyboard()

    def _record_vocals(self):
        app = self._app()
        if app is not None:
            app.prepare_vocal_recording()

    def _workspace_clicked(self, index):
        """User navigation may change playback context; programmatic select() may not."""
        self.select(index)
        app = self._app()
        if app is None:
            return
        if index == 2:
            enter_song_mode(app)
            self.select(2)
            app.status.showMessage("SONG mode · full Arrange timeline playback", 2600)
        elif index == 1:
            app.set_mode("pattern")
            app.status.showMessage("BEAT · current pattern playback", 2200)
        elif index == 5:
            clip = app.playlist.selected_clip
            if clip is not None and clip.kind == "audio":
                app.open_vocal_clip(clip)

    def _master_clicked(self):
        self.select(3)
        self._master_view = True
        app = self._app()
        if app is not None:
            app.mixer.master_strip.setFocus(Qt.OtherFocusReason)
            app.status.showMessage("MASTER · final bus, limiter and output", 2400)

    def _refresh_song_summary(self, *_args):
        self.song_summary.setText(structure_summary(self.song_template.currentText()))

    def _map_full_song(self):
        app = self._app()
        if app is None:
            return
        placed = map_full_song(app, self.song_template.currentText())
        if placed:
            self.tabs.setCurrentIndex(8)
            self.select(2)
            fit_song(app)

    def _sample_to_arrange(self):
        app = self._app()
        if app is None:
            return
        clip = place_sample_selection(app)
        if clip is not None:
            self.tabs.setCurrentIndex(8)
            self.select(2)

    def _fit_arrange(self):
        app = self._app()
        if app is not None:
            fit_song(app)

    def select(self, index):
        if index not in self.pages:
            return
        self.selected = index
        self._master_view = False
        if hasattr(self, "visuals"):
            self.visuals.header.set_page(index)
        self.stack.setCurrentIndex(index)
        for key, button in self.buttons.items():
            button.setChecked(key == index)
        self.beat_tools.setVisible(index == 1)
        self.arrange_tools.setVisible(index == 2)
        self._show_actions(self.song_tools_action.isChecked())
        self.arrange_pattern.setVisible(index in (1, 2, 6))
        self.arrange_pattern.setText("PATTERN → SONG" if index != 2 else "ADD CURRENT PATTERN")
        self.hint.setText(
            {
                1: "Build the beat here · map sounds to lanes, draw hits, then send the pattern to Song.",
                0: "Sampler · trim, chop and audition source audio before mapping it.",
                4: "Instruments · choose and shape a playable source.",
                6: "Performance · compose sample or synth notes in the piano roll.",
                2: "Song · Arrange owns the complete record: sections, patterns, vocals and audio clips.",
                3: "Mix · balance tracks, effects, sends and the master output.",
                5: "Autotune · shape the pitch of an existing recording. Record dry vocals in Song.",
                7: "Automation · draw song-level movement for gain and pan.",
            }[index]
        )

    def activate(self, enabled):
        self.enabled = enabled
        self.tabs.tabBar().setVisible(not enabled)
        for index, page in self.pages.items():
            (self.docks if enabled else self.holders)[index].layout().addWidget(page)
            page.show()

        # Reference target keeps Browser left and rack/Inspector right. Apply
        # those proportions once when Studio opens, never by reparenting, and do
        # not keep overriding user visibility choices after the first layout.
        if enabled and not self._layout_applied:
            app = self._app()
            if app is not None:
                app.main_splitter.setSizes([280, 1100, 290])
            self._layout_applied = True
