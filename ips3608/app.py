"""Native Windows bench controller. Run with python -m ips3608.app."""

from collections import deque
from datetime import datetime
import json
import math
import os
from pathlib import Path
import queue
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from .device import Device, STALE_SECONDS, ports
from .protocol import validate_setpoints, validate_action, PROTECTION_NAMES, MODE_NAMES
from .models import model_for, output_limits, current_reading
from .paths import DATA, ASSETS, migrate_presets
from .branding import APP_NAME, VERSION, REPOSITORY
from .recording import Recorder, Session, Statistics
from .programs import Runner
from .ui import BG, PANEL, FIELD, INK, MUTED, FAINT, ACCENT, RED, FONT, NUMBER_FONT, ScrollArea, label, button

ROOT = DATA
DEFAULT_PRESETS = [
    {"name": "Logic 1.8", "volts": 1.8, "amps": 0.1},
    {"name": "Logic 3.3", "volts": 3.3, "amps": 0.5},
    {"name": "USB 5", "volts": 5.0, "amps": 1.0},
    {"name": "9 volt", "volts": 9.0, "amps": 0.5},
    {"name": "12 volt", "volts": 12.0, "amps": 1.0},
    {"name": "24 volt", "volts": 24.0, "amps": 0.5},
]


class Trace(tk.Canvas):
    def __init__(self, parent, title, field, unit, color, floor):
        super().__init__(parent, bg=PANEL, highlightthickness=0, height=150)
        self.title, self.field, self.unit, self.color, self.floor = title, field, unit, color, floor
        self.data, self.window = [], 60
        self.bind("<Configure>", lambda _: self.draw())

    def update_trace(self, samples, window):
        self.window = window
        self.data = samples
        self.draw()

    def draw(self):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 100 or height < 70:
            return
        left, right, top, bottom = 62, width - 18, 28, height - 30
        latest = self.data[-1]["monotonic"] if self.data else time.monotonic()
        start = latest - self.window
        visible = [s for s in self.data if s["monotonic"] >= start]
        peak = max((max(0, s[self.field]) for s in visible), default=0)
        scale, unit = 1., self.unit
        if self.field == "amps" and self.floor < .001:
            scale, unit = (1e6, "µA") if peak < .001 else (1e3, "mA") if peak < 1 else (1., "A")
        self.create_text(18, 12, text=f"{self.title.split(' · ')[0]} · {unit}", anchor="w", fill=MUTED, font=(FONT, 10))
        ceiling = max(self.floor, peak * 1.15)
        magnitude = 10 ** math.floor(math.log10(ceiling))
        ceiling = math.ceil(ceiling / magnitude * 2) * magnitude / 2
        for i in range(4):
            y = bottom - (bottom - top) * i / 3
            self.create_line(left, y, right, y, fill="#292929")
            self.create_text(left - 10, y, text=f"{ceiling * i / 3 * scale:.3g}", anchor="e",
                             fill=FAINT, font=(FONT, 9))
        for i in range(4):
            x = left + (right - left) * i / 3
            seconds = self.window * (1 - i / 3)
            caption = "Now" if i == 3 else (f"−{seconds / 60:g} min" if seconds >= 60 else f"−{seconds:g} s")
            self.create_text(x, bottom + 18, text=caption, anchor="w" if i == 0 else "e" if i == 3 else "center",
                             fill=FAINT, font=(FONT, 9))
        if not visible:
            self.create_text((left + right) / 2, (top + bottom) / 2,
                             text="Waiting for live readings", fill=FAINT, font=(FONT, 11))
            return
        segment, previous = [], None
        for sample in visible:
            if previous is not None and sample["monotonic"] - previous > STALE_SECONDS:
                if len(segment) >= 4:
                    self.create_line(*segment, fill=self.color, width=2)
                segment = []
            x = left + (sample["monotonic"] - start) / self.window * (right - left)
            y = bottom - max(0, min(ceiling, sample[self.field])) / ceiling * (bottom - top)
            segment.extend((x, y))
            previous = sample["monotonic"]
        if len(segment) >= 4:
            self.create_line(*segment, fill=self.color, width=2)
        elif segment:
            x, y = segment
            self.create_oval(x - 2, y - 2, x + 2, y + 2, fill=self.color, outline="")


class App:
    def __init__(self, root, demo=None):
        self.root = root
        self.demo = demo
        root.title(APP_NAME)
        self.icon = tk.PhotoImage(file=ASSETS / "icon-128.png")
        self.mark_image = tk.PhotoImage(file=ASSETS / "icon-48.png")
        root.iconphoto(True, self.icon)
        root.configure(bg=BG)
        root.geometry("1200x1000")
        root.option_add("*Font", (FONT, 10))
        self.events = queue.Queue()
        if demo:
            from .demo import DemoSerial
            self.device = Device(self.events, serial_factory=lambda **kwargs: DemoSerial(demo, **kwargs))
        else:
            self.device = Device(self.events)
        self.state = {}
        self.connected = self.connecting = self.closing = False
        self.pending = None
        self.last_sample = 0.0
        self.history = deque(maxlen=20000)
        self.session = Session()
        self.statistics = Statistics()
        self.stats_reset_at = 0.0
        self.recorder = None
        self.last_file = None
        self.seeded = False
        self.program = None
        self.device_panel = self.program_panel = None
        self.program_document = None
        self.agent_bridge = None
        self.program_status = tk.StringVar(value="No program running")
        self.presets = self.load_presets()
        self.voltage = tk.StringVar()
        self.current = tk.StringVar()
        self.port = tk.StringVar()
        self.range = tk.StringVar(value="60 seconds")
        self.info = tk.StringVar(value="Connect to read the supply’s current settings.")
        self.build()
        root.update_idletasks()
        # Native font metrics vary with Windows DPI. Reserve the full control
        # rail instead of clipping presets and capture at the default height.
        required = self.wrap.winfo_reqheight() + 12
        height = min(required, root.winfo_screenheight() - 100)
        root.geometry(f"1200x{height}")
        root.minsize(960, min(620, root.winfo_screenheight() - 100))
        self.refresh_ports()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind_all("<F8>", lambda _: self.output_off())
        root.after(80, self.tick)

    def load_presets(self):
        try:
            migrate_presets()
            values = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))["presets"]
            if len(values) != 6:
                raise ValueError()
            for value in values:
                validate_setpoints(float(value["volts"]), float(value["amps"]))
                if not isinstance(value["name"], str) or not value["name"].strip():
                    raise ValueError()
            return values
        except (OSError, ValueError, KeyError, TypeError):
            return [dict(p) for p in DEFAULT_PRESETS]

    def build(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=INK,
                        arrowcolor=MUTED, bordercolor=FIELD, padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)],
                  foreground=[("readonly", INK)], selectbackground=[("readonly", FIELD)],
                  selectforeground=[("readonly", INK)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", INK)
        self.scroll_area = ScrollArea(self.root)
        self.scroll_area.pack(fill="both", expand=True)
        wrap = self.wrap = self.scroll_area.content
        wrap.configure(padx=22, pady=18)
        header = tk.Frame(wrap, bg=BG)
        header.pack(fill="x", pady=(0, 18))
        mark = label(header, image=self.mark_image)
        mark.pack(side="left", padx=(0, 10))
        title = tk.Frame(header, bg=BG)
        title.pack(side="left")
        label(title, APP_NAME, 19, weight="bold").pack(anchor="w")
        self.model_label = label(title, "IPS3608 · DPS-150 · DPS-150P", 10, color=MUTED)
        self.model_label.pack(anchor="w")
        self.connect_btn = button(header, "Connect", self.toggle_connection)
        self.connect_btn.pack(side="right", padx=(8, 0))
        self.port_box = ttk.Combobox(header, textvariable=self.port, width=12, state="readonly")
        self.port_box.pack(side="right", padx=(8, 0))
        self.rescan_btn = button(header, "Find supply", self.refresh_ports)
        self.rescan_btn.pack(side="right")

        tools = tk.Frame(wrap, bg=BG)
        tools.pack(fill="x", pady=(0, 14))
        button(tools, "Device settings", self.open_device_settings).pack(side="left")
        button(tools, "Programs & sweeps", self.open_programs).pack(side="left", padx=(8, 0))
        button(tools, "About", self.open_about).pack(side="left", padx=(0, 8))
        self.device_status = label(tools, "Temperature —  ·  Mode —  ·  Protection —", 10, MUTED)
        self.device_status.pack(side="right")
        self.program_banner = label(wrap, textvariable=self.program_status, size=10, color=MUTED)
        self.program_banner.pack(anchor="w", pady=(0, 10))
        self.agent_label = label(tools, "", 9, FAINT)
        self.agent_label.pack(side="right", padx=(0, 14))

        # Two columns: flexible traces + 300 px control rail, one 14 px gutter.
        body = tk.Frame(wrap, bg=BG)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, minsize=300)
        body.rowconfigure(1, weight=1)
        readings = tk.Frame(body, bg=PANEL, padx=20, pady=18)
        readings.grid(row=0, column=0, sticky="nsew", padx=(0, 14), pady=(0, 14))
        self.readings = {}
        self.reading_units = {}
        self.stat_labels = {}
        for i, (field, name, unit) in enumerate((("volts", "Output voltage", "V"),
                                                ("amps", "Output current", "A"),
                                                ("watts", "Output power", "W"))):
            readings.columnconfigure(i, weight=1, uniform="readings")
            slot = tk.Frame(readings, bg=PANEL)
            slot.grid(row=0, column=i, sticky="ew")
            label(slot, name, 10, MUTED).pack(anchor="w")
            value = label(slot, "—", 28, INK)
            value.configure(font=(NUMBER_FONT, 27))
            value.pack(anchor="w", pady=(6, 0))
            self.reading_units[field] = label(slot, unit, 10, FAINT)
            self.reading_units[field].pack(anchor="w")
            self.readings[field] = value
            stats = tk.Frame(slot, bg=PANEL)
            stats.pack(fill="x", padx=(0, 16), pady=(10, 0))
            stats.columnconfigure(1, weight=1)
            self.stat_labels[field] = {}
            for row, (key, caption) in enumerate((("minimum", "Min"), ("maximum", "Max"), ("average", "Average"))):
                label(stats, caption, 10, MUTED).grid(row=row, column=0, sticky="w", pady=1)
                number = label(stats, "—", 10)
                number.grid(row=row, column=1, sticky="e", padx=(8, 0), pady=1)
                self.stat_labels[field][key] = number
        stats_footer = tk.Frame(readings, bg=PANEL)
        stats_footer.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.stats_caption = label(stats_footer, "0 samples since reset", 9, FAINT)
        self.stats_caption.pack(side="left")
        button(stats_footer, "Reset statistics", self.reset_statistics).pack(side="right")

        charts = tk.Frame(body, bg=PANEL, padx=6, pady=12)
        charts.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        chart_head = tk.Frame(charts, bg=PANEL, padx=12)
        chart_head.pack(fill="x", pady=(0, 6))
        label(chart_head, "Live traces", 12, weight="bold").pack(side="left")
        range_box = ttk.Combobox(chart_head, textvariable=self.range, state="readonly", width=12,
                                values=("60 seconds", "5 minutes", "30 minutes"))
        range_box.pack(side="right")
        self.vtrace = Trace(charts, "Voltage · V", "volts", "V", ACCENT, 1)
        self.vtrace.pack(fill="both", expand=True)
        self.atrace = Trace(charts, "Current · A", "amps", "A", "#e6e6e6", 0.1)
        self.atrace.pack(fill="both", expand=True)

        rail = tk.Frame(body, bg=PANEL, padx=20, pady=18)
        rail.grid(row=0, column=1, rowspan=2, sticky="nsew")
        rail.grid_propagate(False)
        label(rail, "Output control", 12, weight="bold").pack(anchor="w")
        self.output_label = label(rail, "Output unknown", 18)
        self.output_label.pack(anchor="w", pady=(8, 0))
        self.actual_label = label(rail, "Device setpoints: —", 10, MUTED)
        self.actual_label.pack(anchor="w", pady=(2, 12))
        self.on_btn = button(rail, "Turn output on", self.output_on)
        self.on_btn.pack(fill="x", pady=(0, 8))
        self.off_btn = button(rail, "Output off   ·   F8", self.output_off, color=RED)
        self.off_btn.pack(fill="x")
        label(rail, "Setpoints", 12, weight="bold").pack(anchor="w", pady=(18, 8))
        fields = tk.Frame(rail, bg=PANEL)
        fields.pack(fill="x")
        fields.columnconfigure(0, weight=1)
        fields.columnconfigure(1, weight=1)
        for column, (name, var) in enumerate((("Voltage · V", self.voltage), ("Current limit · A", self.current))):
            label(fields, name, 10, MUTED).grid(row=0, column=column, sticky="w", pady=(0, 6))
            entry = tk.Entry(fields, textvariable=var, font=(FONT, 17), bg=FIELD,
                             fg=INK, insertbackground=INK, relief="flat", width=7,
                             highlightthickness=1, highlightbackground=FIELD, highlightcolor=ACCENT)
            entry.grid(row=1, column=column, sticky="ew", ipady=6, padx=(0, 10 if column == 0 else 0))
            entry.bind("<Return>", lambda event: self.apply())
        self.limits_label = label(rail, "Connect to detect model and limits", 10, FAINT, wraplength=260, justify="left")
        self.limits_label.pack(anchor="w", pady=(6, 8))
        actions = tk.Frame(rail, bg=PANEL)
        actions.pack(fill="x")
        self.apply_btn = button(actions, "Apply settings", self.apply, primary=True)
        self.apply_btn.pack(side="left", fill="x", expand=True)
        self.restore_btn = button(actions, "Read back", self.restore)
        self.restore_btn.pack(side="right", padx=(6, 0))
        self.edit_note = label(rail, "Connect to edit", 10, MUTED)
        self.edit_note.pack(anchor="w", pady=(6, 0))
        presets_head = tk.Frame(rail, bg=PANEL)
        presets_head.pack(fill="x", pady=(18, 6))
        label(presets_head, "Presets", 12, weight="bold").pack(side="left")
        self.edit_preset_btn = button(presets_head, "Edit", self.edit_presets)
        self.edit_preset_btn.pack(side="right")
        self.preset_frame = tk.Frame(rail, bg=PANEL)
        self.preset_frame.pack(fill="x")
        self.draw_presets()
        label(rail, "Selecting a preset stages values only.", 9, FAINT).pack(anchor="w", pady=(7, 0))
        self.identity = label(rail, "Firmware —  ·  Hardware —", 9, MUTED)
        self.identity.pack(anchor="w", pady=(16, 0))

        capture = tk.Frame(wrap, bg=PANEL, padx=20, pady=14)
        capture.pack(fill="x", pady=(14, 0))
        top = tk.Frame(capture, bg=PANEL)
        top.pack(fill="x")
        label(top, "Session capture", 12, weight="bold").pack(side="left")
        self.record_btn = button(top, "Start CSV recording", self.toggle_recording)
        self.record_btn.pack(side="right")
        self.folder_btn = button(top, "Open logs", self.open_logs)
        self.folder_btn.pack(side="right", padx=(0, 8))
        self.session_label = label(capture, "0 samples  ·  0.0000 Ah  ·  0.0000 Wh", 11)
        self.session_label.pack(anchor="w", pady=(5, 0))
        self.record_label = label(capture, "Not recording · totals cover received samples in this connection", 9, MUTED)
        self.record_label.pack(anchor="w", pady=(3, 0))
        footer = tk.Frame(wrap, bg=BG)
        footer.pack(fill="x", pady=(12, 0))
        self.status_label = label(footer, "Disconnected", 10, MUTED)
        self.status_label.grid(row=0, column=0, sticky="w")
        label(footer, f"v{VERSION}", 9, FAINT).grid(row=0, column=1, sticky="e")
        self.info_label = label(footer, textvariable=self.info, size=10, color=MUTED,
                               wraplength=1000, justify="left")
        self.info_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        footer.columnconfigure(0, weight=1)
        footer.bind("<Configure>", lambda event: self.info_label.configure(wraplength=max(300, event.width-12)))
        self.update_controls()

    def draw_presets(self):
        for widget in self.preset_frame.winfo_children():
            widget.destroy()
        for i, preset in enumerate(self.presets):
            text = f'{preset["volts"]:g} V · {preset["amps"]:g} A'
            b = button(self.preset_frame, text, lambda p=preset: self.stage(p), anchor="w")
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0, 6 if i % 2 == 0 else 0), pady=(0, 6))
        self.preset_frame.columnconfigure(0, weight=1, uniform="presets")
        self.preset_frame.columnconfigure(1, weight=1, uniform="presets")

    def refresh_ports(self):
        found = ["DEMO"] if self.demo else ports()
        self.port_box["values"] = found
        if self.port.get() not in found:
            self.port.set(found[0] if found else "")
        if not found:
            self.info.set("No supported FNIRSI supply found. Check its USB data connection and power.")
        elif not self.connected:
            self.info.set("Supply found. Connect to read its current settings.")
        self.connect_btn["state"] = "normal" if found and not self.connecting else "disabled"

    def toggle_connection(self):
        if self.connected or self.connecting:
            if self.program and self.program.active:
                self.program.stop()
            self.device.close()
            self.info.set("Disconnecting; output settings are preserved.")
            self.connect_btn["state"] = "disabled"
            return
        if not self.port.get():
            return
        self.state = {}
        self.last_sample = 0
        self.history.clear()
        self.session = Session()
        self.reset_statistics()
        self.seeded = False
        self.pending = None
        self.connecting = True
        self.info.set("Reading identity, output state and setpoints…")
        self.device.start(self.port.get())
        self.update_controls()

    def fresh(self):
        now = time.monotonic()
        return (self.connected and now - self.last_sample <= STALE_SECONDS and
                now - self.state.get("status_monotonic", 0) <= STALE_SECONDS and
                now - self.state.get("settings_monotonic", 0) <= 4.5)

    def validation_state(self):
        """Offline editing may use the full family envelope; live control cannot."""
        return self.state if self.state.get("model") else None

    def reset_statistics(self):
        self.statistics.reset()
        self.stats_reset_at = time.monotonic()
        self.info.set("Min, max and average reset. Recording and session totals continue.")
        self.update_controls()

    def dirty(self):
        try:
            volts, amps = validate_setpoints(float(self.voltage.get()), float(self.current.get()), self.validation_state())
            return (abs(volts - self.state.get("set_volts", -999)) > 0.011 or
                    abs(amps - self.state.get("set_amps", -999)) > 0.0011)
        except ValueError:
            return True

    def stage(self, preset):
        try:
            validate_setpoints(preset["volts"], preset["amps"], self.validation_state(), live=False)
        except ValueError as error:
            self.info.set(str(error))
            return
        self.voltage.set(f'{preset["volts"]:.2f}')
        self.current.set(f'{preset["amps"]:.3f}')
        self.info.set(f'{preset["name"]} staged. Click Apply settings to send the values.')
        self.update_controls()

    def restore(self):
        if "set_volts" in self.state:
            self.voltage.set(f'{self.state["set_volts"]:.2f}')
            self.current.set(f'{self.state["set_amps"]:.3f}')
            self.info.set("Fields match the last device readback.")
            self.device.request("refresh")

    def apply(self):
        if not self.fresh() or self.pending or self.program_running():
            return
        try:
            target = validate_setpoints(float(self.voltage.get()), float(self.current.get()), self.state)
        except ValueError as error:
            self.info.set(str(error))
            return
        self.send_device("set", *target)

    def output_on(self):
        if not self.fresh() or self.pending or self.program_running() or self.dirty() or self.state.get("output") is not False:
            return
        if self.send_device("on"):
            self.info.set("Waiting for output-on confirmation…")

    def output_off(self):
        if self.program and self.program.active:
            self.program.stop()
        if not self.connected:
            return
        self.pending = "off"
        self.device.request("off")
        self.info.set("Output-off command sent; waiting for confirmation…")
        self.update_controls()

    def program_running(self):
        return self.program is not None and self.program.active

    def open_device_settings(self):
        from .panels import DevicePanel
        if self.device_panel and self.device_panel.window.winfo_exists():
            self.device_panel.window.lift()
        else:
            self.device_panel = DevicePanel(self)

    def open_about(self):
        window = tk.Toplevel(self.root)
        window.title(f"About {APP_NAME}")
        window.configure(bg=PANEL, padx=24, pady=22)
        window.transient(self.root)
        window.resizable(False, False)
        label(window, image=self.mark_image).pack(anchor="w", pady=(0, 12))
        label(window, APP_NAME, 20, weight="bold").pack(anchor="w")
        label(window, f"Version {VERSION} · MIT license · Brandon Jank", 10, MUTED).pack(anchor="w", pady=(6, 16))
        label(window, "Control, measure and automate FNIRSI bench power supplies.", 11, wraplength=460).pack(anchor="w")
        label(window, "IPS3608 · DPS-150 · DPS-150P\nIndependent project; not affiliated with FNIRSI.",
              10, MUTED, wraplength=460, justify="left").pack(anchor="w", pady=(8, 16))
        label(window, "Settings, recordings and local agent state", 10, MUTED).pack(anchor="w")
        label(window, str(ROOT), 10, wraplength=460).pack(anchor="w", pady=(4, 16))
        actions = tk.Frame(window, bg=PANEL)
        actions.pack(fill="x")
        button(actions, "Project on GitHub", lambda: webbrowser.open(REPOSITORY)).pack(side="left")
        button(actions, "MCP setup", lambda: webbrowser.open(REPOSITORY + "/blob/main/docs/mcp.md")).pack(side="left", padx=8)
        button(actions, "Close", window.destroy).pack(side="right")
        from .panels import center
        center(window, self.root)

    def open_programs(self):
        from .panels import ProgramPanel
        if self.program_panel and self.program_panel.window.winfo_exists():
            self.program_panel.window.lift()
        else:
            self.program_panel = ProgramPanel(self)

    def send_device(self, action, *args):
        if not self.fresh() or self.pending or self.program_running():
            self.info.set("Wait for fresh readings and finish the active change/program first.")
            return False
        try:
            model_for(self.state.get("model"))
            args = validate_action(action, args, self.state)
            self.device.request(action, *args)
        except ValueError as error:
            self.info.set(str(error))
            return False
        self.pending = action
        self.info.set("Applying device setting; waiting for readback…")
        self.update_controls()
        return True

    def reset_energy(self):
        self.session = Session()
        self.info.set("App Ah/Wh session totals reset. CSV, statistics and device counters continue.")

    def start_program(self, steps, cycles, finish_off):
        if not self.fresh() or self.pending or self.program_running():
            raise ValueError("Connect and finish the current change/program first.")
        if self.state.get("output") is not True:
            raise ValueError("Turn output on explicitly before running a program.")
        model_for(self.state.get("model"))
        self.program = Runner(steps, cycles, finish_off, self.state, live=True)
        self.info.set("Program started. Manual setting changes are locked until it stops.")

    def pause_program(self):
        if self.program_running():
            self.program.pause(time.monotonic())

    def resume_program(self):
        if self.program_running() and self.fresh():
            self.program.resume(time.monotonic())

    def stop_program(self):
        if self.program_running():
            self.program.stop()
        self.output_off()

    def advance_program(self):
        if not self.program:
            return
        now = time.monotonic()
        if self.program.active:
            if not self.fresh() or self.state.get("output") is not True or self.state.get("protection", 0) != 0:
                self.stop_program()
                self.info.set("Program stopped: output, protection or communication state changed. Output off requested.")
            elif self.pending is None:
                target = self.program.tick(now)
                if target:
                    try:
                        validate_setpoints(*target, state=self.state)
                        self.device.request("set", *target)
                        self.voltage.set(f"{target[0]:.2f}")
                        self.current.set(f"{target[1]:.3f}")
                        self.pending = "set"
                    except ValueError as error:
                        self.stop_program()
                        self.info.set(f"Program stopped: {error} Output off requested.")
            if self.program.active:
                try:
                    step = self.program.steps[self.program.index]
                    validate_setpoints(step.volts, step.amps, self.state)
                except ValueError as error:
                    self.stop_program()
                    self.info.set(f"Program stopped: {error} Output off requested.")
        if self.program.phase == "complete" and not self.program.finish_handled:
            self.program.finish_handled = True
            if self.program.finish_off:
                self.pending = "off"
                self.device.request("off")
                self.info.set("Program complete. Output off requested.")
            else:
                self.info.set("Program complete. Final output settings held.")
        self.program_status.set(self.program.status())

    def toggle_recording(self):
        if self.recorder:
            self.stop_recording()
            return
        if not self.fresh():
            return
        logs = ROOT / "logs"
        logs.mkdir(exist_ok=True)
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Record live measurements", initialdir=logs,
            initialfile=datetime.now().strftime(self.state.get("model", "FNIRSI") + "_%Y-%m-%d_%H-%M-%S.csv"),
            defaultextension=".csv", filetypes=(("CSV measurements", "*.csv"),), confirmoverwrite=True)
        if not path:
            return
        # Deliberately refuse overwrite, even if a name was chosen in the dialog.
        try:
            self.recorder = Recorder(path)
            self.last_file = Path(path)
            self.info.set("CSV recording started.")
        except OSError as error:
            self.info.set(f"Could not create recording: {error}")

    def stop_recording(self):
        if self.recorder:
            rows, path = self.recorder.rows, self.recorder.path
            self.recorder.close()
            self.recorder = None
            self.info.set(f"Saved {rows:,} samples to {path.name}.")

    def open_logs(self):
        path = self.last_file.parent if self.last_file else ROOT / "logs"
        path.mkdir(exist_ok=True)
        os.startfile(str(path))

    def edit_presets(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Edit presets")
        dialog.configure(bg=PANEL, padx=20, pady=20)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        inputs = []
        label(dialog, "Local presets · saved on this computer", 12, weight="bold").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))
        for col, title in enumerate(("Name", "Voltage · V", "Current · A")):
            label(dialog, title, 10, MUTED).grid(row=1, column=col, sticky="w", pady=(0, 8))
        for i, preset in enumerate(self.presets):
            row = []
            for col, key in enumerate(("name", "volts", "amps")):
                var = tk.StringVar(value=str(preset[key]))
                tk.Entry(dialog, textvariable=var, width=18 if col == 0 else 10,
                         bg=FIELD, fg=INK, insertbackground=INK, relief="flat").grid(
                             row=i + 2, column=col, padx=(0, 10), pady=5, ipady=7)
                row.append(var)
            inputs.append(row)
        error = label(dialog, "", 10, RED, wraplength=440)
        error.grid(row=8, column=0, columnspan=3, sticky="w", pady=8)

        def save():
            try:
                values = []
                for name, volts, amps in inputs:
                    title = name.get().strip()
                    if not title or len(title) > 16:
                        raise ValueError("Use preset names of 1–16 characters.")
                    v, a = validate_setpoints(float(volts.get()), float(amps.get()), self.validation_state(), live=False)
                    values.append({"name": title, "volts": v, "amps": a})
                temp = ROOT / "settings.json.tmp"
                ROOT.mkdir(parents=True, exist_ok=True)
                temp.write_text(json.dumps({"presets": values}, indent=2), encoding="utf-8")
                temp.replace(ROOT / "settings.json")
                self.presets = values
                self.draw_presets()
                dialog.destroy()
            except (ValueError, OSError) as exc:
                error["text"] = str(exc)

        button(dialog, "Save presets", save, primary=True).grid(row=9, column=0, columnspan=3, sticky="e")
        dialog.grab_set()

    def update_controls(self):
        fresh, busy, dirty = self.fresh(), self.pending is not None, self.dirty()
        model = None
        limits = None
        try:
            model = model_for(self.state.get("model"))
            limits = output_limits(self.state)
        except ValueError:
            pass
        self.model_label["text"] = (model.name if model else self.demo or "IPS3608 · DPS-150 · DPS-150P") + (" · SIMULATED" if self.demo else "")
        self.atrace.floor = .00001 if model and model.microamps else .1
        self.root.title((f"{APP_NAME} · {model.name}" if model else APP_NAME) + (" · DEMO" if self.demo else ""))
        if model:
            caption = f"0–{model.volts:g} V / 0–{model.amps:g} A / {model.watts:g} W"
            if model.input_limited:
                prefix = "Available" if fresh else "Last reported"
                caption += f"\n{prefix}: {limits['voltage_V']:.2f} V / {limits['current_A']:.3f} A" if limits else "\nWaiting for device/input limits"
            self.limits_label["text"] = caption
        else:
            self.limits_label["text"] = "Connect to detect model and limits"
        busy = busy or self.program_running()
        output = self.state.get("output") if fresh else None
        self.connect_btn.configure(text="Disconnect" if self.connected else "Connecting…" if self.connecting else "Connect",
                                   state="disabled" if self.connecting else "normal" if self.port.get() else "disabled")
        self.rescan_btn["state"] = "disabled" if self.connected or self.connecting else "normal"
        self.port_box["state"] = "disabled" if self.connected or self.connecting else "readonly"
        self.apply_btn["state"] = "normal" if fresh and limits and not busy else "disabled"
        self.restore_btn["state"] = "normal" if fresh and not busy else "disabled"
        self.on_btn["state"] = "normal" if fresh and limits and not busy and output is False and not dirty else "disabled"
        self.off_btn["state"] = "normal" if self.connected else "disabled"
        self.record_btn.configure(text="Stop recording" if self.recorder else "Start CSV recording",
                                  state="normal" if fresh or self.recorder else "disabled")
        self.output_label.configure(text="Output on" if output is True else "Output off" if output is False else "Output unknown",
                                    fg=ACCENT if output is True else INK if output is False else MUTED)
        if "set_volts" in self.state:
            self.actual_label["text"] = f'Device: {self.state["set_volts"]:.2f} V · {self.state["set_amps"]:.3f} A'
        else:
            self.actual_label["text"] = "Device setpoints: —"
        self.edit_note["text"] = ("Waiting for confirmation…" if busy else
                                   "Changes apply to live output" if output is True else
                                   "Unapplied values" if fresh and dirty else
                                   "Settings match device" if fresh else "Connect for fresh readings")
        self.identity["text"] = f'Firmware {self.state.get("firmware", "—")}  ·  Hardware {self.state.get("hardware", "—")}'
        temperature = f'{self.state["temperature"]:.1f} °C' if "temperature" in self.state else "—"
        mode = MODE_NAMES.get(self.state.get("mode"), "—")
        protection = PROTECTION_NAMES.get(self.state.get("protection"), "—")
        self.device_status.configure(text=f"{temperature}  ·  {mode}  ·  {protection}", fg=MUTED if fresh else FAINT)
        for key, widget in self.readings.items():
            value, unit = current_reading(self.state[key], model) if key == "amps" and key in self.state else (f'{self.state[key]:.3f}' if key in self.state else "—", {"volts": "V", "amps": "A", "watts": "W"}[key])
            widget.configure(text=value,
                             fg=INK if fresh else FAINT)
            self.reading_units[key]["text"] = unit
            for statistic, number in self.stat_labels[key].items():
                values = getattr(self.statistics, statistic)
                text = f'{values[key]:.3f}' if key in values else "—"
                if key == "amps" and key in values and model and model.microamps:
                    number_value, number_unit = current_reading(values[key], model)
                    text = f"{number_value} {number_unit}"
                number.configure(text=text)
        self.stats_caption["text"] = f'{self.statistics.count:,} samples since reset'
        if fresh:
            age = max(0, time.monotonic() - self.last_sample)
            self.status_label.configure(text=f'{self.port.get()} · Live · {age:.1f} s ago', fg=MUTED)
        elif self.connected:
            self.status_label.configure(text="Readings stale · check supply", fg=RED)
        else:
            self.status_label.configure(text="Connecting…" if self.connecting else "Disconnected", fg=MUTED)
        self.session_label["text"] = (f'{self.session.samples:,} samples  ·  {self.session.amp_hours:.4f} Ah'
                                      f'  ·  {self.session.watt_hours:.4f} Wh  ·  {int(self.session.duration)} s measured')
        if self.recorder:
            self.record_label.configure(text=f'Recording · {self.recorder.path.name} · {self.recorder.rows:,} rows', fg=ACCENT)
        elif self.last_file:
            self.record_label.configure(text=f'Last file · {self.last_file.name}', fg=MUTED)

    def tick(self):
        if self.agent_bridge:
            self.agent_bridge.drain(self)
        for _ in range(500):
            try:
                event, values = self.events.get_nowait()
            except queue.Empty:
                break
            if event in ("state", "sample", "connected", "confirmed"):
                self.state.update({k: v for k, v in values.items()
                                   if k not in ("timestamp", "monotonic", "action", "port")})
            if event == "connected":
                self.connected, self.connecting = True, False
                self.info.set("Connected. Output settings preserved.")
                if not self.seeded:
                    self.voltage.set(f'{values["set_volts"]:.2f}')
                    self.current.set(f'{values["set_amps"]:.3f}')
                    self.seeded = True
            elif event == "sample":
                self.last_sample = values["monotonic"]
                self.history.append(values)
                self.session.add(values)
                if values["monotonic"] >= self.stats_reset_at:
                    self.statistics.add(values)
                if self.recorder:
                    try:
                        self.recorder.add(values)
                    except OSError as error:
                        self.stop_recording()
                        self.info.set(f"Recording stopped: {error}")
            elif event == "confirmed":
                self.pending = None
                if values["action"] == "set" and self.program_running():
                    self.program.confirmed(time.monotonic())
                self.info.set({"set": "Settings confirmed by device readback.",
                               "on": "Output on · confirmed by supply.",
                               "off": "Output off · confirmed by supply."}.get(values["action"],
                               f'{values["action"].capitalize()} confirmed by device readback.'))
            elif event in ("error", "warning"):
                self.pending = None
                if self.program_running():
                    self.stop_program()
                self.info.set(values["message"])
            elif event == "disconnected":
                self.connected = self.connecting = False
                self.pending = None
                if self.recorder:
                    previous_message = self.info.get()
                    self.stop_recording()
                    self.info.set(previous_message + " CSV saved.")
                elif self.info.get().startswith("Disconnecting"):
                    self.info.set("Disconnected. Output settings preserved.")
            if self.agent_bridge:
                self.agent_bridge.on_event(self, event, values)
        self.advance_program()
        self.update_controls()
        seconds = {"60 seconds": 60, "5 minutes": 300, "30 minutes": 1800}[self.range.get()]
        samples = list(self.history)
        self.vtrace.update_trace(samples, seconds)
        self.atrace.update_trace(samples, seconds)
        if self.closing:
            if not self.device.thread or not self.device.thread.is_alive():
                if self.agent_bridge:
                    self.agent_bridge.shutdown()
                    self.agent_bridge = None
                self.root.destroy()
                return
        self.root.after(150, self.tick)

    def close(self):
        if self.closing:
            return
        if self.connected and self.state.get("output") is True:
            if not messagebox.askokcancel("Close bench control", "Output is on. Closing stops any program and leaves output on.\n\nClose the app?", parent=self.root):
                return
        if self.program_running():
            self.program.stop()
        self.closing = True
        self.stop_recording()
        self.device.close()
        self.info.set("Closing connection…")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Control and monitor FNIRSI bench power supplies.")
    parser.add_argument("--demo", nargs="?", const="IPS3608", choices=("IPS3608", "DPS-150", "DPS-150P"),
                        help="Use an in-memory simulated supply. No serial port is opened.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    args = parser.parse_args()
    # Opt into system DPI scaling before creating any Windows controls.
    if os.name == "nt":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("brandonjank.ips3608-control")
    ROOT.mkdir(parents=True, exist_ok=True)
    import logging
    logging.basicConfig(filename=ROOT / "app.log", level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    root = tk.Tk()
    def report_error(kind, value, tb):
        logging.error("UI callback failed", exc_info=(kind, value, tb))
        app.info.set("An interface error occurred. Details were saved in the app data folder.")
    app = App(root, demo=args.demo)
    root.report_callback_exception = report_error
    from .agent_bridge import AppBridge
    bridge = AppBridge()
    try:
        bridge.start()
    except (RuntimeError, OSError) as error:
        messagebox.showerror("IPS3608 agent bridge", str(error), parent=root)
        root.destroy()
        return
    app.agent_bridge = bridge
    app.agent_label["text"] = "Agent bridge ready"
    if args.demo:
        app.info.set("Demo mode uses simulated readings. No USB device is controlled.")
    root.mainloop()


if __name__ == "__main__":
    main()
