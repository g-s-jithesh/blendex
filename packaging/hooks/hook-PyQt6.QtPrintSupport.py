from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, _, datas = add_qt6_dependencies(__file__)
binaries = []
