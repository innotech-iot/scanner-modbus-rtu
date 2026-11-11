import csv
from pathlib import Path
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import innotech_scanner as gui
from test_anhui_gui_core import Sensor

class Device(Sensor):
    def close(self):pass
    def read_table(self,sid,fc,address,count):
        if sid!=self.sid or self.host_baud!=self.baud:raise gui.protocol.CommunicationError('timeout')
        if address==1:raise gui.scan.ExceptionResponse(2)
        return [0 if address==0 else 42]

class ScannerWindowTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        with patch.object(gui.App,'setup_logging'),patch.object(gui.list_ports,'comports',return_value=[SimpleNamespace(device='COM_TEST',description='Simulado')]):
            self.app=gui.App(self.root)
    def tearDown(self):
        if self.app.worker:self.app.worker.join(3)
        self.app.destroy()
    def pump(self):
        deadline=time.monotonic()+3
        while self.app.busy and time.monotonic()<deadline:
            self.root.update();time.sleep(.02)
        self.assertIsNone(self.app.busy)
    def detect(self,bus):
        self.app.max_id.set('8');self.app.scan_baud.set('19200')
        with patch.object(gui.scan,'Bus',return_value=bus):self.app.detect();self.pump()
    def test_discover_explore_filter_and_export_zero_values(self):
        bus=Device();self.detect(bus)
        self.assertEqual(self.app.endpoint,('COM_TEST',19200,'N',7))
        self.assertTrue(self.app.apply_button.instate(['disabled']))
        self.app.start_address.set('0');self.app.end_address.set('2')
        with patch.object(gui.scan,'Bus',return_value=bus):self.app.explore();self.pump()
        self.assertEqual(len(self.app.rows),4)
        self.assertEqual(len(self.app.registers.get_children()),4)
        self.app.nonzero.set(True)
        self.assertEqual(len(self.app.registers.get_children()),2)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'readings.csv'
            with patch.object(gui.filedialog,'asksaveasfilename',return_value=str(path)):self.app.export()
            with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f,delimiter=';'))
            self.assertEqual(len(rows),4)
            self.assertEqual(rows[0]['value'],'0')
        self.assertEqual(bus.writes,[])
    def test_configuration_requires_explicit_documented_profile(self):
        bus=Device();self.detect(bus)
        with patch.object(gui.scan,'Bus') as factory:
            self.app.apply();factory.assert_not_called()
        self.app.profile.set(gui.PROFILE)
        self.assertFalse(self.app.apply_button.instate(['disabled']))
    def test_profile_still_configures_and_verifies_selected_id_and_baud(self):
        bus=Device();self.detect(bus);self.app.profile.set(gui.PROFILE)
        self.app.new_id.set('23');self.app.new_baud.set('38400')
        with patch.object(gui.scan,'Bus',return_value=bus):self.app.apply();self.pump()
        self.assertEqual((bus.sid,bus.baud),(23,38400))
        self.assertIn('Gravação confirmada',self.app.status.get())
    def test_changing_port_clears_readings_and_endpoint(self):
        self.detect(Device());self.app.port.set('COM_OTHER')
        self.assertIsNone(self.app.endpoint);self.assertEqual(self.app.rows,[])
        self.assertTrue(self.app.explore_button.instate(['disabled']))
    def test_invalid_register_range_does_not_access_bus(self):
        self.detect(Device());self.app.start_address.set('65535');self.app.end_address.set('65536')
        with patch.object(gui.scan,'Bus') as factory:self.app.explore();factory.assert_not_called()
    def test_offset_parse_accepts_decimal_and_hex_without_base_guessing(self):
        self.assertEqual(self.app.number('0010'),10)
        self.assertEqual(self.app.number('0x10'),16)

    def test_float_button_uses_discovered_parity_and_never_writes(self):
        class EvenDevice(Device):
            def configure(self, baud, parity):
                super().configure(baud, parity)
                self.host_parity = parity

            def read_table(self, sid, fc, address, count):
                if self.host_parity != 'E' or sid != self.sid:
                    raise gui.protocol.CommunicationError('timeout')
                if address == 0x0016 and count == 2:
                    return [0xC148, 0]
                return super().read_table(sid, fc, address, count)

        bus = EvenDevice()
        self.app.scan_parity.set('Todas')
        self.detect(bus)
        self.assertEqual(self.app.endpoint, ('COM_TEST', 19200, 'E', 7))
        self.assertIn('8E1', self.app.selection_text.get())
        with patch.object(gui.scan, 'Bus', return_value=bus):
            self.app.float_button.invoke()
            self.pump()
        self.assertIn('-12.5', self.app.float_result.get())
        self.assertEqual(bus.writes, [])

    def test_port_selection_prefers_usb_to_bluetooth(self):
        ports = [
            SimpleNamespace(device='COM4', description='Bluetooth', hwid='BTHENUM'),
            SimpleNamespace(device='COM7', description='USB Serial Port', vid=0x0403),
        ]
        with patch.object(gui.list_ports, 'comports', return_value=ports):
            self.app.refresh_ports()
            self.assertEqual(self.app.port.get(), 'COM7')
            self.assertEqual(tuple(self.app.port_box['values']), ('COM7', 'COM4'))
            self.app.port.set('COM4')
            self.app.refresh_ports()
            self.assertEqual(self.app.port.get(), 'COM4')

    def test_bluetooth_open_error_points_to_usb_port(self):
        ports = [
            SimpleNamespace(device='COM4', description='Bluetooth', hwid='BTHENUM'),
            SimpleNamespace(device='COM7', description='USB Serial Port', vid=0x0403),
        ]
        with patch.object(gui.list_ports, 'comports', return_value=ports):
            self.app.refresh_ports()
        self.app.port.set('COM4')
        with patch.object(gui.scan, 'Bus', side_effect=gui.SerialException('Windows 121')):
            self.app.detect()
            self.pump()
        self.assertIn('COM4 (Bluetooth)', self.app.status.get())
        self.assertIn('COM7', self.app.status.get())
        self.assertNotIn('Windows 121', self.app.status.get())
