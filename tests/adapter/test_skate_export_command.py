"""Exporter launch contracts preserve exact argument boundaries."""
from pathlib import Path
import unittest
from skate_cache_worker import export_command


class ExportCommandTests(unittest.TestCase):
    def test_portable_executable_does_not_require_dotnet(self):
        arguments=[Path('E:/Game Files/Paks'),Path('cache with spaces'),'mesh-list']
        executable=Path('portable files/DragonwildsMapExport.EXE')
        command=export_command({'map_export':executable},arguments)
        self.assertEqual(command,[str(executable),str(arguments[0]),str(arguments[1]),'mesh-list'])
        self.assertEqual(len(command),4)

    def test_managed_dll_uses_configured_runtime(self):
        paths={'map_export':Path('export/DragonwildsMapExport.dll'),'dotnet':Path('runtime/dotnet.exe')}
        self.assertEqual(export_command(paths,['one argument with spaces']),
                         [str(paths['dotnet']),str(paths['map_export']),'one argument with spaces'])
        with self.assertRaises(ValueError):export_command({'map_export':'unknown.txt'},[])


if __name__=='__main__':unittest.main()
