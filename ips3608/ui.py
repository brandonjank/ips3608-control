"""Shared native UI tokens and controls."""
import tkinter as tk

BG, PANEL, FIELD = "#000000", "#111111", "#2a2a2a"
INK, MUTED, FAINT, ACCENT, RED = "#ffffff", "#b4b4b4", "#949494", "#f2b33d", "#ef767a"
FONT = "Segoe UI"
NUMBER_FONT = "Consolas"


class ScrollArea(tk.Frame):
    """Expand on desktop, scroll vertically when the display is short."""
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.bar = tk.Scrollbar(self, orient="vertical")
        self.bar.pack(side="right", fill="y")
        self.canvas = tk.Canvas(self, bg=BG, bd=0, highlightthickness=0, yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.configure(command=self.canvas.yview)
        self.content = tk.Frame(self.canvas, bg=BG)
        self.item = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.canvas.bind("<Configure>", self.reflow)
        self.content.bind("<Configure>", self.reflow)
        parent.bind_all("<MouseWheel>", self.wheel, add="+")

    def reflow(self, event=None):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        required = self.content.winfo_reqheight()
        self.canvas.itemconfigure(self.item, width=width, height=max(height, required))
        self.canvas.configure(scrollregion=(0, 0, width, max(height, required)))
        if required > height + 2:
            if not self.bar.winfo_manager():
                self.bar.pack(side="right", fill="y", before=self.canvas)
        elif self.bar.winfo_manager():
            self.bar.pack_forget()
            self.canvas.yview_moveto(0)

    def wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        if (self.bar.winfo_manager() and widget and widget.winfo_toplevel() == self.winfo_toplevel()
                and widget.winfo_class() not in ("Text", "TCombobox", "Listbox")):
            self.canvas.yview_scroll(-int(event.delta / 120), "units")
            return "break"


def label(parent, text="", size=11, color=INK, weight="normal", **kw):
    return tk.Label(parent, text=text, font=(FONT, size, weight), bg=parent["bg"],
                    fg=color, anchor="w", **kw)


def button(parent, text, command, primary=False, color=None, **kw):
    return tk.Button(parent, text=text, command=command, font=(FONT, 10),
                     bg=INK if primary else FIELD, fg=BG if primary else (color or INK),
                     activebackground="#dddddd" if primary else "#383838",
                     activeforeground=BG if primary else INK,
                     disabledforeground="#666666" if primary else "#929292", relief="flat", bd=0,
                     padx=14, pady=8, cursor="hand2", takefocus=True,
                     highlightthickness=1, highlightbackground=parent["bg"],
                     highlightcolor=ACCENT, **kw)


def entry(parent, variable, width=12):
    return tk.Entry(parent, textvariable=variable, width=width, bg=FIELD, fg=INK,
                    insertbackground=INK, relief="flat", highlightthickness=1,
                    highlightbackground=FIELD, highlightcolor=ACCENT)
