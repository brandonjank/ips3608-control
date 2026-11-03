"""Device configuration and timed program editors."""

import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from .protocol import PROTECTIONS, protection_limit, validate_protections, validate_setpoints
from .programs import parse_steps, program_from_dict, program_to_dict, sweep_steps
from .ui import BG, PANEL, FIELD, INK, MUTED, FAINT, ACCENT, RED, FONT, label, button, entry


def center(window, parent):
    window.update_idletasks()
    x = max(0, parent.winfo_rootx() + (parent.winfo_width() - window.winfo_reqwidth()) // 2)
    y = max(0, parent.winfo_rooty() + (parent.winfo_height() - window.winfo_reqheight()) // 2)
    window.geometry(f"+{x}+{y}")


class DevicePanel:
    def __init__(self, app):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title(f"{app.state.get('model', 'FNIRSI')} · Device settings")
        self.window.configure(bg=BG, padx=20, pady=18)
        self.window.transient(app.root)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.after_id = None
        label(self.window, "Device settings", 18, weight="bold").pack(anchor="w", pady=(0, 6))
        label(self.window, "Changes are sent only when you click Apply or Save.", 10, MUTED).pack(anchor="w", pady=(0, 14))
        self.book = ttk.Notebook(self.window)
        self.book.pack(fill="both", expand=True)
        style = ttk.Style()
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=FIELD, foreground=MUTED, padding=(14, 10))
        style.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", INK)])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=INK,
                        rowheight=34, borderwidth=0, font=(FONT, 10))
        style.configure("Treeview.Heading", background=PANEL, foreground=MUTED, relief="flat")
        style.map("Treeview", background=[("selected", FIELD)], foreground=[("selected", INK)])
        self.apply_buttons = []
        self.protection_vars, self.protection_actual = {}, {}
        self.make_protections()
        self.make_presets()
        self.make_display()
        self.make_other()
        footer = tk.Frame(self.window, bg=BG)
        footer.pack(fill="x", pady=(14, 0))
        self.status = label(footer, "", 10, MUTED, wraplength=650, justify="left")
        self.status.pack(anchor="w")
        label(footer, textvariable=app.info, size=10, color=MUTED, wraplength=650,
              justify="left").pack(anchor="w", pady=(6, 0))
        self.read_values()
        self.refresh()
        center(self.window, app.root)

    def tab(self, caption):
        frame = tk.Frame(self.book, bg=PANEL, padx=20, pady=18)
        self.book.add(frame, text=caption)
        return frame

    def make_protections(self):
        frame = self.tab("Protections")
        label(frame, "Device trip thresholds", 12, weight="bold").grid(row=0, column=0, columnspan=3, sticky="w")
        label(frame, "Applied limits affect the live output. UVP monitors the internal input rail.",
              10, MUTED, wraplength=610).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 18))
        for column, text in enumerate(("Protection", "New threshold", "Device readback")):
            label(frame, text, 10, MUTED).grid(row=2, column=column, sticky="w", padx=(0, 20), pady=(0, 10))
        captions = {"ovp": "Overvoltage · V", "ocp": "Overcurrent · A", "opp": "Overpower · W",
                    "otp": "Overtemperature · °C", "uvp": "Input undervoltage · V"}
        for row, key in enumerate(PROTECTIONS, 3):
            label(frame, captions[key], 11).grid(row=row, column=0, sticky="w", padx=(0, 20), pady=10)
            var = self.protection_vars[key] = tk.StringVar()
            entry(frame, var).grid(row=row, column=1, sticky="w", padx=(0, 20), ipady=6)
            self.protection_actual[key] = label(frame, "—", 10, MUTED)
            self.protection_actual[key].grid(row=row, column=2, sticky="w")
        actions = tk.Frame(frame, bg=PANEL)
        actions.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(18, 0))
        button(actions, "Read device values", self.read_values).pack(side="left")
        apply = button(actions, "Apply protections", self.apply_protections, primary=True)
        apply.pack(side="right")
        self.apply_buttons.append((apply, lambda: all(k in self.app.state for k in PROTECTIONS)))

    def apply_protections(self):
        try:
            values = validate_protections({k: float(v.get()) for k, v in self.protection_vars.items()}, self.app.state)
            self.app.send_device("protections", values)
        except ValueError as error:
            self.app.info.set(str(error))

    def make_presets(self):
        frame = self.tab("Device presets")
        label(frame, "Six slots stored in the supply", 12, weight="bold").pack(anchor="w")
        label(frame, "These are separate from the main window’s local presets.", 10, MUTED).pack(anchor="w", pady=(6, 14))
        self.table = ttk.Treeview(frame, columns=("slot", "volts", "amps"), show="headings", height=6, selectmode="browse")
        for name, caption in (("slot", "Slot"), ("volts", "Voltage · V"), ("amps", "Current · A")):
            self.table.heading(name, text=caption)
            self.table.column(name, width=180, anchor="e" if name != "slot" else "w")
        for index in range(1, 7):
            self.table.insert("", "end", iid=str(index), values=(f"M{index}", "—", "—"))
        self.table.pack(fill="x", pady=(0, 18))
        self.table.bind("<<TreeviewSelect>>", self.select_preset)
        self.slot = tk.StringVar(value="1")
        self.preset_v, self.preset_a = tk.StringVar(), tk.StringVar()
        inputs = tk.Frame(frame, bg=PANEL)
        inputs.pack(fill="x")
        for column, (caption, var) in enumerate((("Slot 1–6", self.slot), ("Voltage · V", self.preset_v), ("Current · A", self.preset_a))):
            label(inputs, caption, 10, MUTED).grid(row=0, column=column, sticky="w", pady=(0, 6))
            entry(inputs, var, 14).grid(row=1, column=column, sticky="w", padx=(0, 18), ipady=6)
        actions = tk.Frame(frame, bg=PANEL)
        actions.pack(fill="x", pady=(18, 0))
        button(actions, "Stage in main controls", self.stage_preset).pack(side="left")
        save = button(actions, "Save to device slot", self.save_preset, primary=True)
        save.pack(side="right")
        self.apply_buttons.append((save, lambda: "device_presets" in self.app.state))
        self.table.selection_set("1")

    def select_preset(self, event=None):
        selection = self.table.selection()
        values = self.app.state.get("device_presets")
        if selection and values:
            index = int(selection[0])
            self.slot.set(str(index))
            self.preset_v.set(f"{values[index - 1][0]:.2f}")
            self.preset_a.set(f"{values[index - 1][1]:.3f}")

    def preset_values(self):
        slot = int(self.slot.get())
        if not 1 <= slot <= 6:
            raise ValueError("Choose slot 1–6.")
        volts, amps = validate_setpoints(float(self.preset_v.get()), float(self.preset_a.get()), self.app.validation_state(), live=False)
        return slot, volts, amps

    def stage_preset(self):
        try:
            slot, volts, amps = self.preset_values()
            self.app.stage(dict(name=f"Device M{slot}", volts=volts, amps=amps))
        except ValueError as error:
            self.app.info.set(str(error))

    def save_preset(self):
        try:
            self.app.send_device("preset", *self.preset_values())
        except ValueError as error:
            self.app.info.set(str(error))

    def make_display(self):
        frame = self.tab("Display & metering")
        label(frame, "Display brightness", 12, weight="bold").grid(row=0, column=0, sticky="w")
        label(frame, "Level 1–14", 10, MUTED).grid(row=1, column=0, sticky="w", pady=(6, 10))
        self.brightness = tk.StringVar()
        ttk.Combobox(frame, textvariable=self.brightness, values=tuple(range(1, 15)), state="readonly", width=10).grid(row=2, column=0, sticky="w")
        apply = button(frame, "Apply brightness", self.apply_brightness)
        apply.grid(row=2, column=1, sticky="w", padx=(18, 0))
        self.apply_buttons.append((apply, lambda: "brightness" in self.app.state))
        self.brightness_actual = label(frame, "Device level: —", 10, MUTED)
        self.brightness_actual.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 24))
        label(frame, "Device energy metering", 12, weight="bold").grid(row=4, column=0, columnspan=2, sticky="w")
        self.metering = tk.StringVar(value="On")
        ttk.Combobox(frame, textvariable=self.metering, values=("On", "Off"), state="readonly", width=10).grid(row=5, column=0, sticky="w", pady=(12, 0))
        apply_meter = button(frame, "Apply metering", self.apply_metering)
        apply_meter.grid(row=5, column=1, sticky="w", padx=(18, 0), pady=(12, 0))
        self.apply_buttons.append((apply_meter, lambda: "metering" in self.app.state))
        self.counter_actual = label(frame, "Device counters: —", 11, wraplength=580)
        self.counter_actual.grid(row=6, column=0, columnspan=2, sticky="w", pady=(14, 6))
        label(frame, "Device counters are independent of the app’s session estimates.", 10, MUTED).grid(row=7, column=0, columnspan=2, sticky="w")
        button(frame, "Reset app Ah/Wh totals", self.app.reset_energy).grid(row=8, column=0, columnspan=2, sticky="w", pady=(18, 0))

    def apply_brightness(self):
        try:
            self.app.send_device("brightness", int(self.brightness.get()))
        except ValueError as error:
            self.app.info.set(str(error))

    def apply_metering(self):
        self.app.send_device("metering", self.metering.get() == "On")

    def make_other(self):
        frame = self.tab("Other controls")
        label(frame, "Front-panel functions", 12, weight="bold").pack(anchor="w")
        for caption in (
            "Fan: automatic cooling; no verified USB speed or curve control.",
            "Beeper volume, language and light/dark style: use the System menu.",
            "Button lock: use the supply’s RUN/STOP long press.",
            "Device address and factory reset: use the About / System menu.",
            "Device counter reset: use the supply’s statistics-clear action.",
            "Calibration and firmware flashing are not part of this controller.",
        ):
            label(frame, caption, 11, MUTED, wraplength=600, justify="left").pack(anchor="w", pady=(16, 0))
        label(frame, "Release the PC connection to operate the front panel. Output is preserved.",
              10, INK, wraplength=600, justify="left").pack(anchor="w", pady=(24, 10))
        self.release = button(frame, "Release front-panel controls", self.release_front_panel)
        self.release.pack(anchor="w")

    def release_front_panel(self):
        if self.app.connected:
            self.app.toggle_connection()

    def read_values(self):
        state = self.app.state
        for key, var in self.protection_vars.items():
            var.set(f"{state[key]:g}" if key in state else "")
        self.brightness.set(str(state.get("brightness", "")))
        self.metering.set("On" if state.get("metering") else "Off")
        self.select_preset()
        if self.app.connected and not self.app.pending:
            self.app.device.request("refresh")

    def refresh(self):
        if not self.window.winfo_exists():
            return
        state = self.app.state
        available = self.app.fresh() and not self.app.pending and not self.app.program_running()
        for widget, capability in self.apply_buttons:
            widget["state"] = "normal" if available and capability() else "disabled"
        for key, widget in self.protection_actual.items():
            value = f'{state[key]:g}' if key in state else "—"
            try:
                maximum = protection_limit(key, state)
                widget["text"] = f"{value}  /  max {maximum:g}"
            except ValueError:
                widget["text"] = f"{value}  /  max unavailable"
        self.brightness_actual["text"] = f'Device level: {state.get("brightness", "—")}'
        if "device_ah" in state:
            metering = "on" if state.get("metering") else "off"
            self.counter_actual["text"] = f'Device: {state["device_ah"]:.4f} Ah · {state.get("device_wh", 0):.4f} Wh · metering {metering}'
        for index, values in enumerate(state.get("device_presets", ()), 1):
            self.table.item(str(index), values=(f"M{index}", f"{values[0]:.2f}", f"{values[1]:.3f}"))
        self.release["state"] = "normal" if self.app.connected else "disabled"
        self.status["text"] = ("Live device readback" if self.app.fresh() else "Connect for fresh device values") + f' · Address {state.get("address", "—")}'
        self.window.title(f"{state.get('model', 'FNIRSI')} · Device settings")
        self.after_id = self.window.after(250, self.refresh)

    def close(self):
        if self.after_id:
            self.window.after_cancel(self.after_id)
            self.after_id = None
        self.window.destroy()


class ProgramPanel:
    def __init__(self, app):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title(f"{app.state.get('model', 'FNIRSI')} · Programs & sweeps")
        self.window.configure(bg=BG, padx=22, pady=20)
        self.window.transient(app.root)
        self.window.minsize(780, 740)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.after_id = None
        label(self.window, "Programs & sweeps", 18, weight="bold").pack(anchor="w")
        label(self.window, "Preview the steps, turn output on, then run. Pause holds output; Stop turns it off.",
              10, MUTED, wraplength=800).pack(anchor="w", pady=(6, 16))
        self.generate_fields()
        editor_head = tk.Frame(self.window, bg=BG)
        editor_head.pack(fill="x", pady=(16, 8))
        label(editor_head, "Sequence · volts, amps, hold seconds", 12, weight="bold").pack(side="left")
        button(editor_head, "Load", self.load).pack(side="right")
        button(editor_head, "Save", self.save).pack(side="right", padx=(0, 8))
        self.editor = tk.Text(self.window, height=8, width=78, bg=PANEL, fg=INK,
                              insertbackground=INK, relief="flat", font=(FONT, 11),
                              undo=True, wrap="none", padx=12, pady=12)
        self.editor.pack(fill="both", expand=True)
        initial = app.program_document
        default = f'{app.state.get("set_volts", 0):.2f}, {app.state.get("set_amps", 0):.3f}, 5\n'
        self.editor.insert("1.0", initial[0] if initial else default)
        options = tk.Frame(self.window, bg=BG)
        options.pack(fill="x", pady=12)
        self.cycles = tk.StringVar(value=str(initial[1]) if initial else "1")
        self.finish = tk.StringVar(value="Output off" if not initial or initial[2] else "Hold final settings")
        label(options, "Cycles", 10, MUTED).pack(side="left", padx=(0, 8))
        entry(options, self.cycles, 6).pack(side="left", ipady=6)
        label(options, "On completion", 10, MUTED).pack(side="left", padx=(18, 8))
        ttk.Combobox(options, textvariable=self.finish, values=("Output off", "Hold final settings"),
                     state="readonly", width=20).pack(side="left")
        button(options, "Validate", self.validate).pack(side="right")
        self.summary = label(self.window, "Validate or generate a sequence to review its limits and duration.", 10, MUTED, wraplength=800, justify="left")
        self.summary.pack(anchor="w", pady=(0, 12))
        controls = tk.Frame(self.window, bg=BG)
        controls.pack(fill="x")
        self.run_button = button(controls, "Run program", self.run, primary=True)
        self.run_button.pack(side="left")
        self.pause_button = button(controls, "Pause", app.pause_program)
        self.pause_button.pack(side="left", padx=8)
        self.resume_button = button(controls, "Resume", app.resume_program)
        self.resume_button.pack(side="left")
        self.stop_button = button(controls, "Stop + output off", app.stop_program, color=RED)
        self.stop_button.pack(side="right")
        label(self.window, textvariable=app.program_status, size=11, color=INK, wraplength=800).pack(anchor="w", pady=(16, 0))
        label(self.window, textvariable=app.info, size=10, color=MUTED, wraplength=800).pack(anchor="w", pady=(8, 0))
        self.refresh()
        center(self.window, app.root)

    def generate_fields(self):
        frame = tk.Frame(self.window, bg=PANEL, padx=16, pady=14)
        frame.pack(fill="x")
        label(frame, "Sweep generator", 12, weight="bold").grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 12))
        self.axis = tk.StringVar(value="voltage")
        axis_box = ttk.Combobox(frame, textvariable=self.axis, values=("voltage", "current"), state="readonly", width=12)
        axis_box.grid(row=1, column=0, sticky="w", padx=(0, 10))
        axis_box.bind("<<ComboboxSelected>>", self.change_axis)
        self.sweep = {}
        self.sweep_labels = {}
        for column, (key, caption, value) in enumerate((
            ("start", "Start", self.app.state.get("set_volts", 0)),
            ("end", "End", self.app.state.get("set_volts", 0)),
            ("increment", "Step size", 0.1),
            ("fixed", "Fixed A / V", self.app.state.get("set_amps", 0)),
            ("seconds", "Hold · s", 5),
        ), 1):
            heading = self.sweep_labels[key] = label(frame, caption, 10, MUTED)
            heading.grid(row=1, column=column, sticky="w", padx=(0, 10))
            variable = self.sweep[key] = tk.StringVar(value=str(value))
            entry(frame, variable, 9).grid(row=2, column=column, sticky="ew", padx=(0, 10), pady=(6, 0), ipady=6)
        button(frame, "Build steps", self.generate).grid(row=2, column=0, sticky="w", padx=(0, 10))
        label(frame, "Voltage sweep uses V start/end/step with fixed A; current sweep uses A with fixed V.",
              9, FAINT, wraplength=750).grid(row=3, column=0, columnspan=6, sticky="w", pady=(12, 0))

    def change_axis(self, event=None):
        voltage = self.axis.get() == "voltage"
        value = self.app.state.get("set_volts" if voltage else "set_amps", 0)
        self.sweep["start"].set(str(value))
        self.sweep["end"].set(str(value))
        self.sweep["fixed"].set(str(self.app.state.get("set_amps" if voltage else "set_volts", 0)))
        self.sweep["increment"].set("0.1" if voltage else "0.01")
        self.sweep_labels["fixed"]["text"] = "Fixed A" if voltage else "Fixed V"

    def values(self):
        return parse_steps(self.editor.get("1.0", "end"), self.app.validation_state()), int(self.cycles.get()), self.finish.get() == "Output off"

    def validate(self):
        try:
            steps, cycles, finish = self.values()
            document = program_to_dict(steps, cycles, finish, self.app.validation_state(), live=self.app.fresh())
            duration = sum(step.seconds for step in steps) * cycles
            self.summary.configure(text=f'{len(steps)} steps × {cycles} cycles · {duration:g} s hold time plus readbacks · '
                                   f'{min(s.volts for s in steps):g}–{max(s.volts for s in steps):g} V · '
                                   f'{min(s.amps for s in steps):g}–{max(s.amps for s in steps):g} A · '
                                   f'finish: {self.finish.get()}', fg=INK)
            return document
        except (ValueError, TypeError) as error:
            self.summary.configure(text=str(error), fg=RED)
            return None

    def generate(self):
        try:
            steps = sweep_steps(self.axis.get(), **{key: float(var.get()) for key, var in self.sweep.items()}, state=self.app.validation_state())
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", "\n".join(f"{s.volts:.2f}, {s.amps:.3f}, {s.seconds:g}" for s in steps))
            self.validate()
        except ValueError as error:
            self.summary.configure(text=str(error), fg=RED)

    def run(self):
        if not self.validate():
            return
        try:
            self.app.start_program(*self.values())
            self.remember()
        except ValueError as error:
            self.summary.configure(text=str(error), fg=RED)

    def save(self):
        document = self.validate()
        if document is None:
            return
        path = filedialog.asksaveasfilename(parent=self.window, title="Save program", defaultextension=".json", filetypes=(("Bench program", "*.json"),))
        if path:
            try:
                Path(path).write_text(json.dumps(document, indent=2), encoding="utf-8")
                self.remember()
            except OSError as error:
                self.summary.configure(text=str(error), fg=RED)

    def load(self):
        path = filedialog.askopenfilename(parent=self.window, title="Load program", filetypes=(("Bench program", "*.json"),))
        if path:
            try:
                if Path(path).stat().st_size > 1_000_000:
                    raise ValueError("Program file is too large.")
                steps, cycles, finish = program_from_dict(json.loads(Path(path).read_text(encoding="utf-8")), self.app.validation_state())
                self.editor.delete("1.0", "end")
                self.editor.insert("1.0", "\n".join(f"{s.volts:.2f}, {s.amps:.3f}, {s.seconds:g}" for s in steps))
                self.cycles.set(str(cycles))
                self.finish.set("Output off" if finish else "Hold final settings")
                self.validate()
                self.remember()
            except (ValueError, OSError) as error:
                self.summary.configure(text=str(error), fg=RED)

    def remember(self):
        try:
            self.app.program_document = (self.editor.get("1.0", "end"), int(self.cycles.get()), self.finish.get() == "Output off")
        except ValueError:
            pass

    def close(self):
        self.remember()
        if self.after_id:
            self.window.after_cancel(self.after_id)
            self.after_id = None
        self.window.destroy()

    def refresh(self):
        if not self.window.winfo_exists():
            return
        active = self.app.program_running()
        self.window.title(f"{self.app.state.get('model', 'FNIRSI')} · Programs & sweeps")
        paused = active and self.app.program.paused
        can_start = self.app.fresh() and not self.app.pending and not active and self.app.state.get("output") is True
        self.run_button["state"] = "normal" if can_start else "disabled"
        self.pause_button["state"] = "normal" if active and not paused else "disabled"
        self.resume_button["state"] = "normal" if paused and self.app.fresh() else "disabled"
        self.stop_button["state"] = "normal" if active else "disabled"
        self.after_id = self.window.after(250, self.refresh)
