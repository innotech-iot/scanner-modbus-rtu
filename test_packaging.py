import unittest
from unittest.mock import patch, Mock
import tempfile
import configure_anhui_transducer as a

class PackagingTests(unittest.TestCase):
    def test_no_arguments_does_not_open_serial(self):
        with patch.object(a.sys, 'argv', ['Anhui_ID2_9600.exe']), patch.object(a, 'Bus') as bus:
            self.assertEqual(a.main(), 2)
            bus.assert_not_called()

    def test_explicit_port_targets_two_and_scans_all_standard_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = ['Anhui_ID2_9600.exe', '--port', 'COM99', '--log', tmp + '/test.log']
            with patch.object(a.sys, 'argv', args), patch.object(a.logging, 'FileHandler', return_value=a.logging.NullHandler()), patch.object(a, 'Bus'), patch.object(a, 'scan', return_value=[(9600,'N',7)]) as scan, patch.object(a, 'configure_device') as configure:
                self.assertEqual(a.main(), 0)
                self.assertEqual(list(scan.call_args.args[3]), list(range(1,248)))
                self.assertEqual(configure.call_args.args[2], 2)
