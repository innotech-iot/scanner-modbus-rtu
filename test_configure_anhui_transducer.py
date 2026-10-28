import unittest
import struct
from unittest.mock import patch
import configure_anhui_transducer as a


class Sensor:
    def __init__(self):
        self.sid, self.baud, self.host_baud = 7, 19200, 19200
        self.writes = []
        self.lose_ack = False

    def configure(self, baud, parity):
        self.host_baud = baud

    def read(self, sid, address, count):
        if sid != self.sid or self.host_baud != self.baud:
            raise a.CommunicationError('timeout')
        values = {0: self.sid, 1: a.BAUD_CODES[self.baud], 2: 5, 3: 2, 4: 0xFF85}
        return struct.pack('>' + 'H' * count, *(values[x] for x in range(address, address + count)))

    def write(self, sid, address, value):
        self.writes.append((sid, address, value))
        if address == 0:
            self.sid = value
        elif address == 1:
            self.baud = a.CODE_BAUDS[value]
        else:
            raise AssertionError('Unexpected write')
        if self.lose_ack:
            raise a.CommunicationError('ack lost')


class Tests(unittest.TestCase):
    @patch.object(a.time, 'sleep')
    def test_configure_id_two(self, _):
        bus = Sensor()
        a.configure_device(bus, [(19200, 'N', 7)], new_id=2)
        self.assertEqual(bus.writes, [(7, 0, 2), (2, 1, 3)])
        self.assertEqual((bus.sid, bus.baud), (2, 9600))

    def test_invalid_target_id_never_writes(self):
        for target in (0, 248):
            bus = Sensor()
            with self.assertRaises(ValueError):
                a.configure_device(bus, [(19200, 'N', 7)], new_id=target)
            self.assertEqual(bus.writes, [])

    def test_manual_crc_examples(self):
        self.assertEqual(a.frame(bytes.fromhex('01 03 00 04 00 01')).hex(), '010300040001c5cb')
        self.assertEqual(a.frame(a.write_payload(1, 0, 2)).hex(), '010600000002080b')

    def test_signed_measurement_and_decimal_scale(self):
        self.assertEqual(a.pressure(Sensor(), 7), -1.23)

    @patch.object(a.time, 'sleep')
    def test_configure_and_verify_even_if_ack_lost(self, _):
        bus = Sensor()
        bus.lose_ack = True
        a.configure_device(bus, [(19200, 'N', 7)])
        self.assertEqual(bus.writes, [(7, 0, 1), (1, 1, 3)])
        self.assertEqual((bus.sid, bus.baud), (1, 9600))

    def test_multiple_devices_never_write(self):
        bus = Sensor()
        with self.assertRaises(a.CommunicationError):
            a.configure_device(bus, [(19200, 'N', 7), (19200, 'N', 8)])
        self.assertEqual(bus.writes, [])

    def test_discovery_read_only(self):
        bus = Sensor()
        self.assertEqual(a.scan(bus, [9600, 19200], ['N'], [1, 7]), [(19200, 'N', 7)])
        self.assertEqual(bus.writes, [])

    def test_write_allowlist_and_echo(self):
        bus = object.__new__(a.Bus)
        bus.exchange = lambda payload: a.frame(payload)
        bus.write(1, 1, 3)
        with self.assertRaises(ValueError):
            bus.write(1, 5, 0)
        bus.exchange = lambda payload: a.frame(a.write_payload(1, 1, 4))
        with self.assertRaises(a.CommunicationError):
            bus.write(1, 1, 3)


if __name__ == '__main__':
    unittest.main()
