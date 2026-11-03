"""Read-only hardware smoke check and short CSV capture. No output writes."""

from datetime import datetime
from pathlib import Path
import queue
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ips3608.device import Device, ports
from ips3608.recording import Recorder


def main():
    found = ports()
    port = sys.argv[1] if len(sys.argv) > 1 else found[0] if found else None
    if not port:
        raise SystemExit("No IPS3608 USB serial device found")
    folder = Path(__file__).resolve().parent.parent / "logs"
    folder.mkdir(exist_ok=True)
    path = folder / datetime.now().strftime("connection-check_%Y%m%d_%H%M%S.csv")
    events = queue.Queue()
    device = Device(events)
    recorder = Recorder(path)
    identity = None
    device.start(port)
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            event, values = events.get(timeout=5)
            if event == "error":
                raise RuntimeError(values["message"])
            if event == "connected":
                identity = values
                print({k: values.get(k) for k in ("port", "model", "hardware", "firmware", "output", "set_volts", "set_amps")})
            if event == "sample":
                recorder.add(values)
                print({k: values[k] for k in ("volts", "amps", "watts")})
            if identity and recorder.rows >= 10:
                break
        if not identity or recorder.rows < 3:
            raise RuntimeError("Not enough complete data returned")
    finally:
        device.close()
        device.thread.join(3)
        recorder.close()
    print(f"Saved {recorder.rows} rows: {path}")


if __name__ == "__main__":
    main()
