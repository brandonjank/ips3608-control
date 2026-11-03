"""Authenticated, loopback-only IPC between MCP and the Tk-owned controller.

Only the GUI thread touches App/Tk or dispatches serial actions. HTTP threads
submit expiring requests and wait for the same device readback as the UI.
"""

from concurrent.futures import Future, TimeoutError, CancelledError
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import queue
import secrets
import threading
import time
import urllib.error
import urllib.request

from .device import ports
from .programs import Step
from .protocol import MODE_NAMES, PROTECTION_NAMES, validate_setpoints, validate_protections
from .recording import Recorder
from .models import MODELS, model_for, output_limits
from .paths import DATA

ROOT = DATA
DISCOVERY = ROOT / ".runtime" / "agent-bridge.json"
REQUEST_TIMEOUT = 8.0


def capabilities(state=None, *, fresh=False):
    model, limits, error = None, None, None
    if state and state.get("model"):
        try:
            model = model_for(state["model"])
            if fresh:
                limits = output_limits(state)
        except ValueError as failure:
            error = str(failure)
    return {
        "device": "FNIRSI bench power supplies", "active_model": model.name if model else None,
        "supported_models": list(MODELS), "models": {key: value.as_dict() for key, value in MODELS.items()},
        "rated_limits": model.as_dict() if model else None,
        "limits": limits, "limits_fresh": bool(fresh and limits), "limits_error": error,
        "supported": ["identity", "live telemetry", "history", "statistics", "CSV recording",
                      "live setpoints", "output on/off", "protection thresholds", "device presets",
                      "brightness", "device metering", "timed sequences", "voltage/current sweeps"],
        "unavailable_in_app": ["fan speed/curve", "volume", "language", "device theme", "key lock",
                                 "address write", "factory reset", "device counter reset", "firmware/calibration"],
        "behavior": {"connect_disconnect_preserves_output": True, "setpoints_preserve_output": True,
                     "program_requires_output_already_on": True, "pause_holds_output": True,
                     "stop_program_requests_output_off": True, "stdio_client_exit_preserves_output": True},
    }


def _fields(params, allowed, required=()):
    if not isinstance(params, dict) or set(params) - set(allowed) or set(required) - set(params):
        raise ValueError("Missing or unsupported parameters.")


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")
    return value


def _integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}.")
    return value


@dataclass
class Request:
    operation: str
    params: dict
    expires: float = field(default_factory=lambda: time.monotonic() + REQUEST_TIMEOUT)
    future: Future = field(default_factory=Future)


class BridgeClient:
    """Local IPC client. Never opens a serial port or starts/enables output."""

    def __init__(self, discovery=DISCOVERY):
        self.discovery = Path(discovery)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, operation, **params):
        try:
            endpoint = json.loads(self.discovery.read_text(encoding="utf-8"))
            port, token = endpoint["port"], endpoint["token"]
            _integer(port, "port", 1, 65535)
            if not isinstance(token, str) or len(token) < 32:
                raise ValueError("Invalid bridge credential")
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise RuntimeError("Open IPS3608 Control first; its agent bridge is not available.") from error
        # The discovery file supplies only a port, never a remote hostname/URL.
        path = "/health" if operation == "health" else "/rpc"
        data = None if operation == "health" else json.dumps({"operation": operation, "params": params}, allow_nan=False).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=REQUEST_TIMEOUT + 2) as response:
                result = json.load(response)
        except (OSError, ValueError, urllib.error.URLError) as error:
            raise RuntimeError("The app bridge is unavailable or timed out. Read status before retrying a change.") from error
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "The app rejected the request."))
        return result["result"]


class AppBridge:
    def __init__(self, discovery=DISCOVERY):
        self.discovery = Path(discovery)
        self.requests = queue.Queue(maxsize=64)
        self.waiting = None
        self.server = self.thread = None
        self.token = secrets.token_urlsafe(32)
        self.stopped = False

    def start(self):
        if self.discovery.exists():
            try:
                if BridgeClient(self.discovery).call("health").get("service") == "ips3608-app":
                    raise RuntimeError("Another IPS3608 controller is already running. Use its existing window.")
            except RuntimeError as error:
                if str(error).startswith("Another IPS3608"):
                    raise
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Never log credentials or request bodies.

            def send_result(self, value, status=200):
                data = json.dumps(value, allow_nan=False).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def authorized(self):
                expected_host = f"127.0.0.1:{self.server.server_port}"
                return (not bridge.stopped and self.headers.get("Host") == expected_host and
                        self.headers.get("Origin") is None and
                        secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {bridge.token}"))

            def do_GET(self):
                if not self.authorized():
                    self.send_result({"ok": False, "error": "Unauthorized"}, 403)
                elif self.path == "/health":
                    self.send_result({"ok": True, "result": {"service": "ips3608-app", "version": 1, "pid": os.getpid()}})
                else:
                    self.send_result({"ok": False, "error": "Not found"}, 404)

            def do_POST(self):
                if not self.authorized():
                    self.send_result({"ok": False, "error": "Unauthorized"}, 403)
                    return
                if self.path != "/rpc":
                    self.send_result({"ok": False, "error": "Not found"}, 404)
                    return
                request = None
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 131072:
                        raise ValueError("Request size must be 1–131,072 bytes.")
                    self.connection.settimeout(3)
                    body = json.loads(self.rfile.read(size))
                    _fields(body, ("operation", "params"), ("operation", "params"))
                    if not isinstance(body["operation"], str) or not isinstance(body["params"], dict):
                        raise ValueError("Invalid request.")
                    request = Request(body["operation"], body["params"])
                    bridge.requests.put_nowait(request)
                    result = request.future.result(timeout=REQUEST_TIMEOUT + 0.5)
                    self.send_result({"ok": True, "result": result})
                except TimeoutError:
                    # If not claimed by Tk, cancellation prevents a late write.
                    cancelled = request.future.cancel() if request else True
                    self.send_result({"ok": False, "error": "Request expired before dispatch." if cancelled else
                                      "Readback was not confirmed in time. Read status before retrying; output may have changed."})
                except (ValueError, RuntimeError, OSError, queue.Full, CancelledError) as error:
                    self.send_result({"ok": False, "error": str(error) or "App request queue is busy."})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.discovery.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.discovery.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "pid": os.getpid(), "port": self.server.server_port,
                                         "token": self.token}), encoding="utf-8")
        temporary.replace(self.discovery)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.1), daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stopped = True
        if self.waiting:
            self.waiting[1].future.set_exception(RuntimeError("App closing; readback was not confirmed."))
            self.waiting = None
        while True:
            try:
                request = self.requests.get_nowait()
            except queue.Empty:
                break
            request.future.cancel()
        if self.server:
            if self.thread and self.thread.is_alive():
                self.server.shutdown()
            self.server.server_close()
        try:
            if json.loads(self.discovery.read_text(encoding="utf-8")).get("token") == self.token:
                self.discovery.unlink()
        except (OSError, ValueError):
            pass

    def snapshot(self, app):
        now = time.monotonic()
        state = dict(app.state)
        fresh = app.fresh()
        latest = dict(app.history[-1]) if app.history else None
        caps = capabilities(state if app.connected else None, fresh=fresh)
        return {
            "connected": app.connected, "connecting": app.connecting, "fresh": fresh,
            "simulated": bool(app.demo),
            "port": app.port.get() if app.connected or app.connecting else None,
            "telemetry_age_s": round(max(0, now - app.last_sample), 3) if app.last_sample else None,
            "output_enabled": state.get("output") if fresh else None,
            "last_reported_output_enabled": state.get("output"),
            "mode": MODE_NAMES.get(state.get("mode")),
            "protection": PROTECTION_NAMES.get(state.get("protection")),
            "device": state, "latest_sample": latest, "pending_change": app.pending,
            "model_profile": caps["rated_limits"], "limits": caps["limits"],
            "controls_available": bool(fresh and caps["active_model"] and caps["limits"]),
            "limits_error": caps["limits_error"],
            "statistics": {"samples": app.statistics.count, "min": dict(app.statistics.minimum),
                           "max": dict(app.statistics.maximum), "average": dict(app.statistics.average)},
            "session": {"samples": app.session.samples, "amp_hours": app.session.amp_hours,
                        "watt_hours": app.session.watt_hours, "measured_seconds": app.session.duration},
            "program": {"active": app.program_running(), "status": app.program.status() if app.program else "No program",
                        "paused": app.program.paused if app.program else False},
            "recording": {"active": app.recorder is not None,
                          "path": str(app.recorder.path) if app.recorder else None,
                          "rows": app.recorder.rows if app.recorder else 0,
                          "last_file": str(app.last_file) if app.last_file else None},
        }

    def drain(self, app):
        if self.waiting and time.monotonic() > self.waiting[1].expires:
            self.waiting[1].future.set_exception(RuntimeError("Readback timed out. Inspect status before retrying."))
            self.waiting = None
        for _ in range(16):
            try:
                request = self.requests.get_nowait()
            except queue.Empty:
                break
            if time.monotonic() > request.expires or self.stopped:
                request.future.cancel()
                continue
            if not request.future.set_running_or_notify_cancel():
                continue
            try:
                result = self.dispatch(app, request)
                if result is not None:
                    request.future.set_result(result)
            except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
                request.future.set_exception(RuntimeError(str(error)))

    def on_event(self, app, event, values):
        if not self.waiting:
            return
        action, request = self.waiting
        if ((event == "confirmed" and values.get("action") == action) or
                (event == "connected" and action == "connect") or
                (event == "disconnected" and action == "disconnect")):
            self.waiting = None
            request.future.set_result({"confirmed": True, "status": self.snapshot(app)})
        elif event in ("error", "warning", "disconnected"):
            self.waiting = None
            request.future.set_exception(RuntimeError(values.get("message", "Connection lost before confirmation.")))

    def ready(self, app):
        if not app.fresh():
            raise ValueError("Fresh connected telemetry and status are required.")
        model_for(app.state.get("model"))
        if app.pending or self.waiting or app.program_running():
            raise ValueError("A change or program is already in progress.")

    def wait_for(self, action, request):
        self.waiting = (action, request)

    def dispatch(self, app, request):
        op, p = request.operation, request.params
        if app.closing and op not in ("capabilities", "status", "ports", "history"):
            raise ValueError("The app is closing; no changes are accepted.")
        if op == "capabilities":
            _fields(p, ())
            result = capabilities(app.state if app.connected else None, fresh=app.fresh())
            result["simulated"] = bool(app.demo)
            return result
        if op == "status":
            _fields(p, ())
            return self.snapshot(app)
        if op == "ports":
            _fields(p, ())
            return {"ports": ["DEMO"] if app.demo else ports()}
        if op == "history":
            _fields(p, ("limit", "seconds"))
            limit = _integer(p.get("limit", 100), "limit", 1, 1000)
            seconds = _number(p.get("seconds", 60), "seconds")
            if not 0 < seconds <= 3600:
                raise ValueError("History window must be 0–3,600 seconds.")
            cutoff = time.monotonic() - seconds
            samples = [dict(s) for s in app.history if s["monotonic"] >= cutoff][-limit:]
            return {"samples": samples, "count": len(samples), "fresh": app.fresh()}
        if op == "connect":
            _fields(p, ("port",))
            if app.connected:
                if p.get("port") and p["port"] != app.port.get():
                    raise ValueError("Disconnect the current supply before selecting another port.")
                return {"confirmed": True, "status": self.snapshot(app)}
            if app.connecting or self.waiting or app.closing:
                raise ValueError("Connection is already changing.")
            found = ["DEMO"] if app.demo else ports()
            port = p.get("port") or (found[0] if len(found) == 1 else None)
            if port not in found:
                raise ValueError("Select a FNIRSI supply port returned by list_ports.")
            app.port.set(port)
            app.toggle_connection()
            self.wait_for("connect", request)
            return None
        if op == "disconnect":
            _fields(p, ())
            if not app.connected and not app.connecting:
                return {"confirmed": True, "status": self.snapshot(app)}
            if self.waiting:
                raise ValueError("Wait for the active request before disconnecting.")
            app.toggle_connection()
            self.wait_for("disconnect", request)
            return None
        if op == "output":
            _fields(p, ("enabled",), ("enabled",))
            if not isinstance(p["enabled"], bool):
                raise ValueError("enabled must be true or false.")
            if p["enabled"]:
                self.ready(app)
                if app.state.get("output") is True:
                    return {"confirmed": True, "status": self.snapshot(app)}
                app.output_on()
                if app.pending != "on":
                    raise ValueError("Output was not enabled. Resolve staged UI values before enabling output.")
                self.wait_for("on", request)
            else:
                if not app.connected:
                    raise ValueError("No serial connection; physical output off cannot be confirmed.")
                if self.waiting:
                    self.waiting[1].future.set_exception(RuntimeError("Superseded by explicit output off; inspect status."))
                    self.waiting = None
                app.output_off()
                self.wait_for("off", request)
            return None
        if op in ("setpoints", "protections", "preset", "brightness", "metering"):
            self.ready(app)
            if op == "setpoints":
                _fields(p, ("voltage_v", "current_a"))
                if not p or all(v is None for v in p.values()):
                    raise ValueError("Provide voltage_v and/or current_a.")
                volts = app.state["set_volts"] if p.get("voltage_v") is None else _number(p["voltage_v"], "voltage_v")
                amps = app.state["set_amps"] if p.get("current_a") is None else _number(p["current_a"], "current_a")
                target = validate_setpoints(volts, amps, app.state)
                app.voltage.set(f"{target[0]:.2f}")
                app.current.set(f"{target[1]:.3f}")
                action, args = "set", target
            elif op == "protections":
                _fields(p, ("ovp", "ocp", "opp", "otp", "uvp"))
                if not p:
                    raise ValueError("Provide at least one protection threshold.")
                values = {key: app.state[key] for key in ("ovp", "ocp", "opp", "otp", "uvp")}
                values.update({k: _number(v, k) for k, v in p.items()})
                action, args = "protections", (validate_protections(values, app.state),)
            elif op == "preset":
                _fields(p, ("slot", "voltage_v", "current_a"), ("slot", "voltage_v", "current_a"))
                slot = _integer(p["slot"], "slot", 1, 6)
                target = validate_setpoints(_number(p["voltage_v"], "voltage_v"), _number(p["current_a"], "current_a"), app.state, live=False)
                action, args = "preset", (slot, *target)
            elif op == "brightness":
                _fields(p, ("level",), ("level",))
                action, args = "brightness", (_integer(p["level"], "level", 1, 14),)
            else:
                _fields(p, ("enabled",), ("enabled",))
                if not isinstance(p["enabled"], bool):
                    raise ValueError("enabled must be true or false.")
                action, args = "metering", (p["enabled"],)
            if not app.send_device(action, *args):
                raise ValueError(app.info.get())
            self.wait_for(action, request)
            return None
        if op in ("reset_statistics", "reset_energy"):
            _fields(p, ())
            getattr(app, op)()
            return self.snapshot(app)
        if op == "record_start":
            _fields(p, ("filename",))
            if not app.fresh():
                raise ValueError("Connect for fresh readings before recording.")
            if app.recorder:
                raise ValueError("A recording is already active.")
            name = p.get("filename") or datetime.now().strftime(app.state.get("model", "FNIRSI") + "_agent_%Y%m%d_%H%M%S_%f.csv")
            if not isinstance(name, str) or len(name) > 120 or Path(name).name != name or not name.lower().endswith(".csv") or any(c in name for c in '\\/:*?"<>|'):
                raise ValueError("Use a plain .csv filename; recordings stay in the app logs folder.")
            stem = name.partition(".")[0].rstrip(" .").upper()
            reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            reserved.update(prefix + digit for prefix in ("COM", "LPT") for digit in "0123456789¹²³")
            if stem in reserved or any(ord(c) < 32 for c in name):
                raise ValueError("Use a regular filename, not a reserved Windows device name.")
            folder = ROOT / "logs"
            folder.mkdir(exist_ok=True)
            app.recorder = Recorder(folder / name)
            app.last_file = app.recorder.path
            app.info.set("Agent started CSV recording.")
            return self.snapshot(app)
        if op == "record_stop":
            _fields(p, ())
            path, rows = (str(app.recorder.path), app.recorder.rows) if app.recorder else (None, 0)
            app.stop_recording()
            return {"saved_path": path, "rows": rows, "status": self.snapshot(app)}
        if op == "program_start":
            _fields(p, ("steps", "cycles", "finish_off"), ("steps",))
            self.ready(app)
            if not isinstance(p["steps"], list) or not 1 <= len(p["steps"]) <= 1000:
                raise ValueError("Provide 1–1,000 steps.")
            steps = []
            for item in p["steps"]:
                _fields(item, ("volts", "amps", "seconds"), ("volts", "amps", "seconds"))
                steps.append(Step(*(_number(item[k], k) for k in ("volts", "amps", "seconds"))))
            cycles = _integer(p.get("cycles", 1), "cycles", 1, 1000)
            finish = p.get("finish_off", True)
            if not isinstance(finish, bool):
                raise ValueError("finish_off must be true or false.")
            app.start_program(steps, cycles, finish)
            return {"accepted": True, "status": self.snapshot(app)}
        if op == "program_control":
            _fields(p, ("action",), ("action",))
            action = p["action"]
            if action not in ("pause", "resume", "stop"):
                raise ValueError("Choose pause, resume or stop.")
            if action == "stop":
                return self.dispatch(app, Request("output", {"enabled": False}, request.expires, request.future))
            if not app.program_running():
                raise ValueError("No active program.")
            if action == "resume" and not app.fresh():
                raise ValueError("Fresh readings are required to resume.")
            getattr(app, f"{action}_program")()
            return self.snapshot(app)
        raise ValueError("Unsupported operation. No arbitrary register, shell or file operations are exposed.")
