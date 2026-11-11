"""Generic RTU read-only discovery/exploration. No vendor register assumptions.
Modbus Application Protocol V1.1b3, FC01/02/03/04.
Vendor-specific writes remain confined to anhui_gui_core's documented profile.
"""
from itertools import product
import struct
import configure_anhui_transducer as p
import anhui_gui_core as vendor

TABLES={1:'Coils / saídas digitais',2:'Entradas digitais',3:'Holding Registers',4:'Input Registers'}

class Cancelled(Exception): pass

class ExceptionResponse(p.ModbusError):
    def __init__(self,code):
        self.code=code
        super().__init__(f'Exceção Modbus 0x{code:02X}')

class Bus(vendor.Bus):
    def exchange(self,payload):
        p.time.sleep(max(.01,3.5*11/self.port.baudrate))
        self.port.reset_input_buffer()
        request=p.frame(payload)
        p.LOG.debug('TX %s',request.hex(' '))
        started=p.time.monotonic()
        self.port.write(request);self.port.flush()
        response=bytearray()
        while p.time.monotonic()-started<self.timeout:
            response.extend(self.port.read(256))
            expected=None
            if len(response)>=2:
                if response[1]&0x80:expected=5
                elif response[1]==6:expected=8
                elif response[1] in TABLES and len(response)>=3:expected=5+response[2]
            if expected and len(response)>=expected:break
        p.LOG.debug('RX %s',response.hex(' ') or '<timeout>')
        if len(response)<5:raise p.CommunicationError('Timeout ou resposta incompleta')
        if p.crc16(response[:-2])!=int.from_bytes(response[-2:],'little'):
            raise p.CommunicationError('CRC inválido')
        if response[0]!=payload[0]:raise p.CommunicationError('Slave ID de resposta inesperado')
        if response[1]==(payload[1]|0x80) and len(response)==5:
            raise ExceptionResponse(response[2])
        if response[1]!=payload[1]:raise p.CommunicationError('Função de resposta inesperada')
        return bytes(response)

    def read_table(self,sid,fc,address,count=1):
        maximum=2000 if fc in (1,2) else 125
        if fc not in TABLES or not 1<=sid<=247 or not 0<=address<=65535 or not 1<=count<=maximum or address+count>65536:
            raise ValueError('Função, ID ou faixa de leitura inválida')
        response=self.exchange(struct.pack('>BBHH',sid,fc,address,count))
        size=(count+7)//8 if fc in (1,2) else count*2
        if response[2]!=size or len(response)!=size+5:
            raise p.CommunicationError('Quantidade de bytes inesperada')
        raw=response[3:-2]
        if fc in (1,2):return [(raw[i//8]>>(i%8))&1 for i in range(count)]
        return list(struct.unpack('>'+'H'*count,raw))

def reference(fc,address):
    # Conventional five-digit notation is ambiguous beyond 9999 offsets.
    return f'{ {1:0,2:10000,3:40000,4:30000}[fc]+address+1:05d}' if address<9999 else '—'

def discover(bus,baudrates,ids,fc,address,cancel,progress,*,parities=('N',)):
    if fc not in TABLES or not 0<=address<=65535:raise ValueError('Consulta de descoberta inválida')
    settings = tuple(product(baudrates, parities))
    ids=tuple(ids)
    total=len(settings)*len(ids);done=0;found=[]
    for baud, parity in settings:
        if cancel.is_set():raise Cancelled()
        bus.configure(baud,parity)
        for sid in ids:
            if cancel.is_set():raise Cancelled()
            responded=False;detail='leitura válida'
            try:
                bus.read_table(sid,fc,address,1)
                responded=True
            except ExceptionResponse as error:
                # Even an illegal address/function confirms an RTU endpoint.
                responded=True;detail=str(error)
            except p.CommunicationError:pass
            if responded:
                found.append((baud,parity,sid))
                p.LOG.info('Encontrado ID=%d / %d baud / 8%s1: %s',sid,baud,parity,detail)
            done+=1
            progress(done,total,baud,sid,tuple(found))
    if cancel.is_set():raise Cancelled()
    return found

def read_anhui_float32(bus, sid):
    fc = 3
    try:
        words = bus.read_table(sid, fc, 0x0016, 2)
    except ExceptionResponse as error:
        if error.code not in (1, 2):
            raise
        fc = 4
        words = bus.read_table(sid, fc, 0x0016, 2)
    value, = struct.unpack('>f', struct.pack('>HH', *words))
    return fc, value


def explore(bus,sid,tables,start,end,cancel,on_row,progress):
    tables=tuple(tables)
    if not tables or any(fc not in TABLES for fc in tables) or not 0<=start<=end<=65535:
        raise ValueError('Selecione uma tabela e uma faixa de 0 a 65535')
    total=len(tables)*(end-start+1);done=0
    summary={'valid':0,'invalid':0,'timeouts':0,'unsupported':[],'exceptions':0}
    for fc in tables:
        for address in range(start,end+1):
            if cancel.is_set():raise Cancelled()
            try:
                value=bus.read_table(sid,fc,address,1)[0]
            except ExceptionResponse as error:
                if error.code==1:
                    summary['unsupported'].append(fc)
                    done+=end-address+1
                    p.LOG.info('FC%02d não suportada; tabela ignorada',fc)
                    progress(done,total,fc,address,dict(summary));break
                if error.code==2:summary['invalid']+=1
                else:summary['exceptions']+=1
            except p.CommunicationError:summary['timeouts']+=1
            else:
                summary['valid']+=1
                on_row({'fc':fc,'table':TABLES[fc],'address':address,'reference':reference(fc,address),
                        'value':value,'signed':value-65536 if fc in (3,4) and value>=32768 else value,
                        'hex':f'0x{value:04X}' if fc in (3,4) else str(value)})
            done+=1
            progress(done,total,fc,address,dict(summary))
    return summary
