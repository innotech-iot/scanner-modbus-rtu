import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import anhui_gui as gui
from test_anhui_gui_core import Sensor


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk(); self.root.withdraw()
        with patch.object(gui.App,'setup_logging'), patch.object(gui.list_ports,'comports',return_value=[SimpleNamespace(device='COM_TEST',description='Simulado')]):
            self.app=gui.App(self.root)
    def tearDown(self):
        if self.app.worker: self.app.worker.join(3)
        self.app.destroy()
    def pump(self):
        deadline=time.monotonic()+3
        while self.app.busy and time.monotonic()<deadline:
            self.root.update(); time.sleep(.02)
        self.assertIsNone(self.app.busy)
    def test_detect_then_apply_selected_values_using_simulated_sensor(self):
        bus=Sensor(); bus.close=lambda:None
        self.app.max_id.set('8'); self.app.scan_baud.set('19200')
        self.assertTrue(self.app.apply_button.instate(['disabled']))
        with patch.object(gui.core,'Bus',return_value=bus):
            self.app.detect(); self.pump()
            self.assertEqual(self.app.found,[(19200,'N',7)])
            self.assertEqual(bus.writes,[])
            self.assertFalse(self.app.apply_button.instate(['disabled']))
            self.app.new_id.set('42'); self.app.new_baud.set('57600')
            self.app.apply(); self.pump()
        self.assertEqual((bus.sid,bus.baud),(42,57600))
        self.assertIn('Sucesso',self.app.status.get())
        self.assertEqual(self.app.found,[(57600,'N',42)])
    def test_port_change_invalidates_previous_detection(self):
        self.app.found=[(9600,'N',2)]; self.app.detected_port='COM_TEST'
        self.app.set_busy(None)
        self.assertFalse(self.app.apply_button.instate(['disabled']))
        self.app.port.set('COM_OTHER')
        self.assertEqual(self.app.found,[])
        self.assertTrue(self.app.apply_button.instate(['disabled']))
    def test_no_port_does_not_start_worker(self):
        self.app.port.set('')
        self.app.detect()
        self.assertIsNone(self.app.worker)
        self.assertIn('Selecione',self.app.status.get())
    def test_invalid_target_does_not_open_serial(self):
        self.app.found=[(9600,'N',2)]; self.app.detected_port='COM_TEST'
        self.app.new_id.set('0')
        with patch.object(gui.core,'Bus') as bus:
            self.app.apply(); bus.assert_not_called()
    def test_serial_error_is_reported_and_does_not_enable_apply(self):
        with patch.object(gui.core,'Bus',side_effect=OSError('Porta ocupada')):
            self.app.detect(); self.pump()
        self.assertIn('Porta ocupada',self.app.status.get())
        self.assertTrue(self.app.apply_button.instate(['disabled']))
    def test_cancelled_scan_cannot_enable_apply(self):
        bus=Sensor(); bus.close=lambda:None
        def read(*args):
            time.sleep(.02)
            raise gui.protocol.CommunicationError('timeout')
        bus.read=read
        with patch.object(gui.core,'Bus',return_value=bus):
            self.app.detect(); self.app.stop(); self.pump()
        self.assertEqual(bus.writes,[])
        self.assertTrue(self.app.apply_button.instate(['disabled']))
        self.assertIn('interrompida',self.app.status.get())
