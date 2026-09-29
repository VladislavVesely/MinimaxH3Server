# Native only: run via python scripts/build_controller.py on the target OS.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH).parent
payload = root / 'build' / 'controller-payload'
datas = collect_data_files('h3server', includes=['web/*', 'backends/*.json', 'backends/*.txt'])
datas += copy_metadata('h3server')
datas += [(str(path), 'h3server/payload') for path in payload.iterdir()]
a = Analysis([str(root / 'src/h3server/__main__.py')], pathex=[str(root / 'src')],
             binaries=[], datas=datas,
             hiddenimports=['uvicorn.logging', 'uvicorn.loops.asyncio',
                            'uvicorn.protocols.http.h11_impl', 'uvicorn.lifespan.on'],
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['torch', 'transformers', 'diffusers'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='h3-controller',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, disable_windowed_traceback=False, argv_emulation=False,
          target_arch=None, codesign_identity=None, entitlements_file=None)
