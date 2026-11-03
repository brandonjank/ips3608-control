# Two entry points share one portable folder. GUI has no console; MCP has stdio.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

root = Path(SPECPATH).parent
icon = str(root / 'ips3608' / 'assets' / 'app.ico')
version = str(root / 'packaging' / 'windows-version.txt')
gui = Analysis([str(root / 'tools' / 'run_gui.py')], pathex=[str(root)],
               datas=[(str(root / 'ips3608' / 'assets'), 'ips3608/assets')],
               excludes=['mcp', 'pydantic', 'PIL', 'capstone', 'pytest'],
               noarchive=False)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True,
              name='IPS3608Control', console=False, icon=icon, version=version,
              upx=False, disable_windowed_traceback=False)
mcp = Analysis([str(root / 'tools' / 'run_mcp.py')], pathex=[str(root)],
               datas=copy_metadata('mcp', recursive=True),
               hiddenimports=collect_submodules('mcp', filter=lambda name: not name.startswith('mcp.cli')) + ['anyio._backends._asyncio'],
               excludes=['PIL', 'capstone', 'pytest'], noarchive=False)
mcp_exe = EXE(PYZ(mcp.pure), mcp.scripts, [], exclude_binaries=True,
              name='IPS3608ControlMCP', console=True, icon=icon, version=version, upx=False)
COLLECT(gui_exe, mcp_exe, gui.binaries, gui.datas, mcp.binaries, mcp.datas,
        name='IPS3608-Control', upx=False)
