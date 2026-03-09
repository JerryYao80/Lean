import importlib.util
import unittest
from pathlib import Path


def load_module():
    module_path = Path(__file__).resolve().parents[3] / 'Scripts' / 'ashare_t1_live_paper.py'
    if not module_path.exists():
        raise AssertionError(f'Expected module to exist: {module_path}')

    spec = importlib.util.spec_from_file_location('ashare_t1_live_paper', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AShareT1LivePaperTests(unittest.TestCase):
    def test_build_launcher_command_targets_local_launcher_binary(self):
        module = load_module()

        command, workdir = module.build_launcher_command()

        self.assertTrue(command[0].endswith('Launcher/bin/Debug/QuantConnect.Lean.Launcher'))
        self.assertEqual(command[1], '--config')
        self.assertTrue(command[2].endswith('Launcher/config/config-ashare-t1-live-paper.json'))
        self.assertTrue(str(workdir).endswith('Launcher/bin/Debug'))

    def test_build_tui_command_uses_repo_python_entry(self):
        module = load_module()

        command = module.build_tui_command(python_executable='/usr/bin/python3')

        self.assertEqual(command[0], '/usr/bin/python3')
        self.assertTrue(command[1].endswith('Scripts/ashare_t1_tui.py'))
        self.assertTrue(command[2].endswith('Launcher/config/config-ashare-t1-live-paper.json'))

    def test_build_launcher_command_preserves_custom_config_path(self):
        module = load_module()

        custom_config = Path('/tmp/config-ashare-t1-momentum-live-paper.json')
        command, _ = module.build_launcher_command(custom_config)

        self.assertEqual(command[2], str(custom_config.resolve()))


if __name__ == '__main__':
    unittest.main()
