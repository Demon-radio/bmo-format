# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['apps\\desktop\\viewer.py'],
    pathex=[],
    binaries=[],
    datas=[('tests/conformance', 'kit_conformance'), ('renderer/python/bmo_render', 'kit_renderer/bmo_render'), ('converters/python/bmo_ooxml', 'kit_converters/bmo_ooxml'), ('calc/python/bmo_calc', 'kit_calc/bmo_calc')],
    hiddenimports=['xml.etree.ElementTree', 'csv'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BMOViewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['apps\\web\\bmo.ico'],
)
