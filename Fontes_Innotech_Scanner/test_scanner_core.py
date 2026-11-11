import struct
import threading
import unittest
from unittest.mock import patch
import scanner_core as s
import configure_anhui_transducer as p

class Port:
    baudrate=9600
    def __init__(self, response): self.response=response; self.sent=[]
    def reset_input_buffer(self): pass
    def write(self,data): self.sent.append(data)
    def flush(self): pass
    def read(self,count):
        data,self.response=self.response,b''
        return data

def bus_for(response):
    bus=object.__new__(s.Bus); bus.port=Port(response); bus.timeout=.005
    return bus

class ScannerTests(unittest.TestCase):
    @patch.object(p.time,'sleep')
    def test_all_four_read_tables_decode_values_and_send_read_only_functions(self,_):
        for fc,raw,expected in [(1,b'\x01',[1]),(2,b'\x00',[0]),(3,b'\xff\xff',[65535]),(4,b'\x12\x34',[4660])]:
            with self.subTest(fc=fc):
                bus=bus_for(p.frame(bytes([7,fc,len(raw)])+raw))
                self.assertEqual(bus.read_table(7,fc,12,1),expected)
                self.assertEqual(bus.port.sent[0],p.frame(struct.pack('>BBHH',7,fc,12,1)))

    @patch.object(p.time,'sleep')
    def test_coils_decode_least_significant_bit_first(self,_):
        bus=bus_for(p.frame(bytes([7,1,2,0x85,1])))
        self.assertEqual(bus.read_table(7,1,0,9),[1,0,1,0,0,0,0,1,1])

    @patch.object(p.time,'sleep')
    def test_crc_and_bytecount_and_wrong_id_are_rejected(self,_):
        frames=[b'\x07\x04\x02\x00\x01\x00\x00',p.frame(bytes([7,4,1,1])),p.frame(bytes([8,4,2,0,1]))]
        for response in frames:
            with self.assertRaises(p.CommunicationError):bus_for(response).read_table(7,4,0,1)

    @patch.object(p.time,'sleep')
    def test_exception_has_code_and_validated_crc(self,_):
        bus=bus_for(p.frame(bytes([7,0x83,2])))
        with self.assertRaises(s.ExceptionResponse) as caught:bus.read_table(7,3,0,1)
        self.assertEqual(caught.exception.code,2)

    def test_invalid_read_ranges_and_write_function_never_send(self):
        bus=bus_for(b'')
        for fc,start,count in [(6,0,1),(3,-1,1),(3,65535,2),(4,0,126),(1,0,2001),(3,0,0)]:
            with self.assertRaises(ValueError):bus.read_table(7,fc,start,count)
        self.assertEqual(bus.port.sent,[])

    def test_generic_discovery_accepts_exception_without_assuming_vendor_registers(self):
        class Device:
            def configure(self,*args): pass
            def read_table(self,sid,*args):
                if sid==5:raise s.ExceptionResponse(2)
                raise p.CommunicationError('timeout')
        progress=[]
        found=s.discover(Device(),[9600],range(1,7),3,0,threading.Event(),lambda *x:progress.append(x))
        self.assertEqual(found,[(9600,'N',5)])
        self.assertEqual(progress[-1][:2],(6,6))

    def test_explore_finds_sparse_and_zero_values_without_any_write(self):
        class Device:
            def read_table(self,sid,fc,address,count):
                if address==1:raise s.ExceptionResponse(2)
                return [0 if address==0 else 65535]
        rows=[]; progress=[]
        summary=s.explore(Device(),7,[3,4],0,2,threading.Event(),rows.append,lambda *x:progress.append(x))
        self.assertEqual(len(rows),4)
        self.assertEqual(rows[0]['value'],0)
        self.assertEqual(rows[1]['signed'],-1)
        self.assertEqual(rows[1]['address'],2)
        self.assertEqual(summary['invalid'],2)
        self.assertEqual(progress[-1][:2],(6,6))

    def test_unsupported_function_is_skipped_without_claiming_empty_table(self):
        class Device:
            def read_table(self,*args):raise s.ExceptionResponse(1)
        summary=s.explore(Device(),7,[4],0,100,threading.Event(),lambda row:None,lambda *x:None)
        self.assertEqual(summary['unsupported'],[4])
        self.assertEqual(summary['valid'],0)

    def test_cancel_stops_register_exploration(self):
        cancel=threading.Event();cancel.set()
        with self.assertRaises(s.Cancelled):
            s.explore(object(),7,[3],0,100,cancel,lambda row:None,lambda *x:None)

    def test_modbus_reference_has_explicit_table_and_offset(self):
        self.assertEqual(s.reference(3,0),'40001')
        self.assertEqual(s.reference(4,0),'30001')
        self.assertEqual(s.reference(1,0),'00001')
        self.assertEqual(s.reference(2,0),'10001')
        self.assertEqual(s.reference(3,65535),'—')

    def test_discovery_reports_even_and_odd_endpoints(self):
        class Device:
            def configure(self, baud, parity):
                self.parity = parity

            def read_table(self, sid, fc, address, count):
                if (self.parity, sid) not in [('E', 1), ('O', 2)]:
                    raise p.CommunicationError('timeout')
                return [sid]

        progress = []
        found = s.discover(Device(), [9600], [1, 2], 3, 0, threading.Event(),
                           lambda *x: progress.append(x), parities=('N', 'E', 'O'))
        self.assertEqual(found, [(9600, 'E', 1), (9600, 'O', 2)])
        self.assertEqual(progress[-1][:2], (6, 6))

    @patch.object(p.time, 'sleep')
    def test_anhui_float_reads_two_registers_in_one_request(self, _):
        payload = bytes([7, 3, 4]) + struct.pack('>f', -12.625)
        bus = bus_for(p.frame(payload))
        self.assertEqual(s.read_anhui_float32(bus, 7), (3, -12.625))
        self.assertEqual(bus.port.sent, [p.frame(struct.pack('>BBHH', 7, 3, 0x16, 2))])

    def test_anhui_float_tries_fc04_when_fc03_is_unsupported(self):
        class Device:
            def read_table(self, sid, fc, address, count):
                if fc == 3:
                    raise s.ExceptionResponse(1)
                return [0x3FC0, 0]

        self.assertEqual(s.read_anhui_float32(Device(), 7), (4, 1.5))
