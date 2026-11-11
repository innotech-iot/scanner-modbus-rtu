import struct
import threading
import unittest
from unittest.mock import patch
import anhui_gui_core as g
import configure_anhui_transducer as p


class Sensor:
    def __init__(self, sid=7, baud=19200, lose_ack=False):
        self.sid, self.baud, self.host_baud = sid, baud, baud
        self.lose_ack = lose_ack
        self.writes = []
    def configure(self, baud, parity):
        self.host_baud = baud
    def read(self, sid, address, count):
        if sid != self.sid or self.host_baud != self.baud:
            raise p.CommunicationError('timeout')
        values = {0:self.sid, 1:p.BAUD_CODES[self.baud], 2:5, 3:2, 4:0xFF85}
        return struct.pack('>'+'H'*count, *(values[i] for i in range(address,address+count)))
    def write(self, sid, address, value):
        if sid != self.sid or self.host_baud != self.baud:
            raise AssertionError('Writing with wrong communication settings')
        self.writes.append((sid,address,value))
        if address == 0: self.sid=value
        elif address == 1: self.baud=p.CODE_BAUDS[value]
        elif (address, value) != (0x000F, 0): raise AssertionError('Undocumented write')
        if self.lose_ack: raise p.CommunicationError('ACK lost')


class CoreTests(unittest.TestCase):
    @patch.object(p.time,'sleep')
    def test_all_documented_baudrates_and_id_are_verified_after_lost_ack(self, _):
        for baud in p.BAUD_CODES:
            with self.subTest(baud=baud):
                bus=Sensor(lose_ack=True)
                value=g.configure(bus,[(19200,'N',7)],23,baud)
                self.assertEqual((bus.sid,bus.baud,value),(23,baud,-1.23))
                expected=[(7,0,23)]
                if baud!=19200: expected.append((23,1,p.BAUD_CODES[baud]))
                self.assertEqual(bus.writes,expected)

    @patch.object(p.time,'sleep')
    def test_unchanged_settings_do_not_write(self, _):
        bus=Sensor()
        g.configure(bus,[(19200,'N',7)],7,19200)
        self.assertEqual(bus.writes,[])

    @patch.object(p.time, 'sleep')
    def test_save_uses_verified_new_id_and_baud(self, _):
        bus = Sensor()
        original_write = bus.write
        def accept_save(sid, address, value):
            if address == 0x000F:
                self.assertEqual((sid, value, bus.host_baud), (23, 0, 9600))
            else:
                original_write(sid, address, value)
        bus.write = unittest.mock.Mock(side_effect=accept_save)
        g.configure(bus, [(19200, 'N', 7)], 23, 9600, save=True)
        self.assertEqual(bus.write.call_args_list, [
            unittest.mock.call(7, 0, 23),
            unittest.mock.call(23, 1, 3),
            unittest.mock.call(23, 0x000F, 0),
        ])

    @patch.object(p.time, 'sleep')
    def test_unconfirmed_save_is_reported_without_retry(self, _):
        bus = Sensor()
        bus.write = unittest.mock.Mock(side_effect=p.CommunicationError('timeout'))
        with self.assertRaisesRegex(p.CommunicationError, 'grava'):
            g.configure(bus, [(19200, 'N', 7)], 7, 19200, save=True)
        bus.write.assert_called_once_with(7, 0x000F, 0)

    @patch.object(p.time, 'sleep')
    def test_save_with_updated_units_and_four_decimals(self, _):
        bus = Sensor()
        original_read = bus.read

        def read(sid, address, count):
            data = original_read(sid, address, count)
            return {2: b'\x00\x11', 3: b'\x00\x04'}.get(address, data)

        bus.read = read
        with self.assertLogs(p.LOG, 'INFO') as logs:
            value = g.configure(bus, [(19200, 'N', 7)], 23, 19200,
                                save=True, updated_manual=True)
        self.assertAlmostEqual(value, -0.0123)
        self.assertTrue(any('-0.0123 cm' in line for line in logs.output))
        self.assertEqual(bus.writes[-1], (23, 0x000F, 0))

    @patch.object(p.time, 'sleep')
    def test_save_when_measurement_is_unavailable(self, _):
        bus = Sensor()
        original_read = bus.read

        def read(sid, address, count):
            if address == 4:
                raise p.CommunicationError('measurement timeout')
            return original_read(sid, address, count)

        bus.read = read
        self.assertIsNone(g.configure(bus, [(19200, 'N', 7)], 23, 19200,
                                      save=True, updated_manual=True))
        self.assertEqual(bus.writes, [(7, 0, 23), (23, 0x000F, 0)])

    def test_invalid_values_and_ambiguous_detection_never_write(self):
        for found,sid,baud in [([],2,9600),([(9600,'N',1),(9600,'N',2)],2,9600), ([(19200,'N',7)],0,9600), ([(19200,'N',7)],248,9600), ([(19200,'N',7)],2,14400)]:
            bus=Sensor()
            with self.assertRaises((ValueError,p.CommunicationError)):
                g.configure(bus,found,sid,baud)
            self.assertEqual(bus.writes,[])

    def test_write_allowlist_includes_only_documented_addresses_and_codes(self):
        bus=object.__new__(g.Bus)
        bus.exchange=lambda payload:p.frame(payload)
        bus.write(7, 0x000F, 0)
        for code in p.CODE_BAUDS: bus.write(7,1,code)
        for address,value in [(0x000F,1),(1,8),(1,-1),(2,1),(5,0),(0,0),(0,248)]:
            with self.assertRaises(ValueError): bus.write(7,address,value)

    def test_scan_is_read_only_and_reports_progress(self):
        bus=Sensor()
        progress=[]
        found=g.discover(bus,[9600,19200],range(1,9),threading.Event(),lambda *args:progress.append(args))
        self.assertEqual(found,[(19200,'N',7)])
        self.assertEqual(bus.writes,[])
        self.assertEqual(progress[-1][:2],(16,16))

    def test_cancel_invalidates_partial_scan(self):
        bus=Sensor(); cancel=threading.Event()
        def tick(*args): cancel.set()
        with self.assertRaises(g.ScanCancelled):
            g.discover(bus,[19200],range(1,9),cancel,tick)
        self.assertEqual(bus.writes,[])

    @patch.object(p.time,'sleep')
    def test_stale_sensor_settings_fail_before_any_write(self, _):
        bus=Sensor(sid=9)
        with self.assertRaises(p.CommunicationError):
            g.configure(bus,[(19200,'N',7)],2,9600)
        self.assertEqual(bus.writes,[])

    def test_mismatched_id_or_baud_register_is_not_a_detection(self):
        bus=Sensor()
        original=bus.read
        def bad_read(sid,address,count):
            return struct.pack('>H',99) if address==0 else original(sid,address,count)
        bus.read=bad_read
        found=g.discover(bus,[19200],[7],threading.Event(),lambda *args:None)
        self.assertEqual(found,[])
