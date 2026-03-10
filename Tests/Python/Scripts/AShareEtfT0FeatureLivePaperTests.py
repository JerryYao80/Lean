import importlib.util
import json
import unittest
import tempfile
from pathlib import Path


def load_module():
    module_path = Path(__file__).resolve().parents[3] / 'Scripts' / 'ashare_etf_t0_feature_live_paper.py'
    if not module_path.exists():
        raise AssertionError(f'Expected module to exist: {module_path}')

    spec = importlib.util.spec_from_file_location('ashare_etf_t0_feature_live_paper', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReadyProcess:
    def poll(self):
        return None


class AShareEtfT0FeatureLivePaperTests(unittest.TestCase):
    def test_load_live_paper_runtime_config_reads_parameter_paths(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / 'Launcher' / 'config' / 'config.json'
            config_path.parent.mkdir(parents=True)
            config_path.write_text(json.dumps({
                'parameters': {
                    'feature-data-path': '../../../Data/alternative/ashare-etf-t0-live-features',
                    'live-feature-report-file': '../../../Results/bridge.json',
                }
            }), encoding='utf-8')

            module.repo_root = lambda: root
            runtime = module.load_live_paper_runtime_config(config_path)

        self.assertEqual(runtime['feature-data-path'], root / 'Data' / 'alternative' / 'ashare-etf-t0-live-features')
        self.assertEqual(runtime['live-feature-report-file'], root / 'Results' / 'bridge.json')

    def test_wait_for_bridge_ready_detects_bootstrap_feature_files(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            feature_dir = root / 'Data' / 'alternative' / 'ashare-etf-t0-live-features' / 'sse' / 'daily'
            feature_dir.mkdir(parents=True)
            (feature_dir / '510300.csv').write_text('trade_date\n20240101\n', encoding='utf-8')
            config_path = root / 'Launcher' / 'config' / 'config.json'
            config_path.parent.mkdir(parents=True)
            config_path.write_text(json.dumps({
                'parameters': {
                    'feature-data-path': '../../../Data/alternative/ashare-etf-t0-live-features',
                }
            }), encoding='utf-8')

            module.repo_root = lambda: root
            ready = module.wait_for_bridge_ready(config_path, ReadyProcess(), timeout_seconds=0.2, poll_interval_seconds=0.01)

        self.assertTrue(ready)

    def test_build_bridge_command_targets_live_bridge_script(self):
        module = load_module()

        command = module.build_bridge_command(python_executable='/usr/bin/python3')

        self.assertEqual(command[0], '/usr/bin/python3')
        self.assertTrue(command[1].endswith('Scripts/ashare_etf_t0_feature_live_bridge.py'))
        self.assertEqual(command[2], '--config')
        self.assertTrue(command[3].endswith('Launcher/config/config-ashare-etf-t0-feature-live-paper.json'))

    def test_build_launcher_command_targets_new_live_paper_config(self):
        module = load_module()

        command, workdir = module.build_launcher_command()

        self.assertTrue(command[0].endswith('Launcher/bin/Debug/QuantConnect.Lean.Launcher'))
        self.assertEqual(command[1], '--config')
        self.assertTrue(command[2].endswith('Launcher/config/config-ashare-etf-t0-feature-live-paper.json'))
        self.assertTrue(str(workdir).endswith('Launcher/bin/Debug'))

    def test_build_launcher_command_preserves_custom_config_path(self):
        module = load_module()

        custom_config = Path('/tmp/config-ashare-etf-t0-feature-live-paper.json')
        command, _ = module.build_launcher_command(custom_config)

        self.assertEqual(command[2], str(custom_config.resolve()))


if __name__ == '__main__':
    unittest.main()
