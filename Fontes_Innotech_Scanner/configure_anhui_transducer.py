"""ANHUI pressure/level transmitter: discovery and ID=2 / 9600 8N1.
Source: RS485 modbus for pressure level sensor.pdf, pages 1-3.
FC03: ID=0, baud code=1, unit=2, decimals=3, signed measurement=4.
FC06: ID register 0, baud code register 1 (9600 -> code 3).
Only connect one sensor. Default mode writes; --scan-only never writes.
No serial port is opened at import time. Requires pyserial.
"""
import argparse
import logging
from pathlib import Path
import struct
import sys
import time
from datetime import datetime

import serial

LOG = logging.getLogger("anhui")
BAUDRATES = (9600, 19200, 4800, 38400, 1200, 2400, 57600, 115200)


class CommunicationError(Exception):
    """No usable response or unverified configuration."""


class ModbusError(CommunicationError):
    """A CRC-validated Modbus exception."""


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def frame(payload):
    return payload + struct.pack("<H", crc16(payload))


BAUD_CODES = {1200: 0, 2400: 1, 4800: 2, 9600: 3, 19200: 4, 38400: 5, 57600: 6, 115200: 7}
CODE_BAUDS = {v: k for k, v in BAUD_CODES.items()}


def write_payload(sid, address, value):
    return struct.pack(">BBHH", sid, 6, address, value)


class Bus:
    def __init__(self, port_name, timeout):
        self.timeout = timeout
        self.port = serial.Serial(port=None, baudrate=9600, bytesize=8,
                                  parity="N", stopbits=1, timeout=0.02,
                                  write_timeout=1, xonxoff=False,
                                  rtscts=False, dsrdtr=False)
        self.port.dtr = False
        self.port.rts = False
        self.port.port = port_name
        self.port.open()

    def close(self):
        self.port.close()

    def configure(self, baud, parity):
        self.port.baudrate = baud
        self.port.parity = parity
        # >=3.5 character times including parity, plus settling time.
        time.sleep(max(0.05, 3.5 * 11 / baud))
        self.port.reset_input_buffer()
        LOG.info("PORTA %s: %d 8%s1", self.port.port, baud, parity)

    def exchange(self, payload):
        time.sleep(max(0.01, 3.5 * 11 / self.port.baudrate))
        self.port.reset_input_buffer()
        request = frame(payload)
        LOG.info("TX %s", request.hex(" ").upper())
        started = time.monotonic()
        self.port.write(request)
        self.port.flush()
        response = bytearray()
        while time.monotonic() - started < self.timeout:
            response.extend(self.port.read(256))
            if len(response) >= 2:
                if response[1] & 0x80:
                    expected = 5
                elif response[1] == 0x06:
                    expected = 8
                elif response[1] == 3 and len(response) >= 3:
                    expected = 5 + response[2]
                else:
                    expected = None
                if expected and len(response) >= expected:
                    break
        elapsed = (time.monotonic() - started) * 1000
        LOG.info("RX %s (%.1f ms)", response.hex(" ").upper() or "<vazio>", elapsed)
        if not response:
            raise CommunicationError("timeout")
        if len(response) < 5:
            raise CommunicationError("resposta incompleta")
        if crc16(response[:-2]) != int.from_bytes(response[-2:], "little"):
            raise CommunicationError("CRC invalido")
        if response[0] != payload[0]:
            raise CommunicationError("Slave ID de resposta inesperado")
        if response[1] == (payload[1] | 0x80) and len(response) == 5:
            raise ModbusError(f"excecao Modbus 0x{response[2]:02X}")
        if response[1] != payload[1]:
            raise CommunicationError("funcao de resposta inesperada")
        return bytes(response)

    def read(self, sid, address, count):
        response = self.exchange(struct.pack(">BBHH", sid, 3, address, count))
        if response[2] != count * 2 or len(response) != count * 2 + 5:
            raise CommunicationError("quantidade de bytes inesperada")
        return response[3:-2]

    def write(self, sid, address, value):
        # Only an ID in the normal unicast range or 9600 baud can be written.
        if not ((address == 0 and 1 <= value <= 247) or (address, value) == (1, 3)):
            raise ValueError("Escrita fora dos parametros autorizados")
        payload = write_payload(sid, address, value)
        response = self.exchange(payload)
        if response[:-2] != payload:
            raise CommunicationError("confirmacao de escrita inesperada")


def read_word(bus, sid, address):
    return struct.unpack(">H", bus.read(sid, address, 1))[0]


LEGACY_UNITS = ("sem unidade", "cm", "mm", "MPa", "Pa", "kPa", "mA")
UPDATED_UNITS = (
    "MPa", "kPa", "Pa", "bar", "mbar", "kgf/cm²", "psi", "mH₂O",
    "mmH₂O", "inH₂O", "H₂O", "mHg", "mmHg", "inHg", "atm", "Torr",
    "m", "cm", "mm", "kg", "°C", "pH", "°F", "sem unidade",
)


def pressure(bus, sid, *, updated_manual=False):
    unit = read_word(bus, sid, 2)
    decimals = read_word(bus, sid, 3)
    units = UPDATED_UNITS if updated_manual else LEGACY_UNITS
    max_decimals = 4 if updated_manual else 3
    if decimals > max_decimals or unit >= len(units):
        raise CommunicationError(
            f"Unidade/casas decimais fora do perfil ANHUI: {unit}/{decimals}"
        )
    raw = struct.unpack(">h", bus.read(sid, 4, 1))[0]
    value = raw / (10 ** decimals)
    LOG.info("MEDICAO ID=%d: %s %s (Int16=%d; decimais=%d)", sid, value, units[unit], raw, decimals)
    return value


def scan(bus, baudrates, parities, ids):
    ids = tuple(ids)
    found = []
    for baud in baudrates:
        for parity in parities:
            bus.configure(baud, parity)
            for sid in ids:
                LOG.info("SCAN %d 8%s1 ID=%d", baud, parity, sid)
                try:
                    actual_id = read_word(bus, sid, 0)
                    baud_code = read_word(bus, sid, 1)
                    if actual_id != sid or CODE_BAUDS.get(baud_code) != baud:
                        raise CommunicationError("ID/baud lidos nao conferem com a consulta")
                    pressure(bus, sid)
                except CommunicationError as error:
                    LOG.info("RESULTADO %d 8%s1 ID=%d: %s", baud, parity, sid, error)
                else:
                    LOG.info("RESULTADO %d 8%s1 ID=%d: resposta valida", baud, parity, sid)
                    found.append((baud, parity, sid))
    LOG.info("SCAN CONCLUIDO: %s", found or "nenhuma resposta valida")
    return found


def verify(bus, baud, sid, *, check_measurement=True, parity="N"):
    bus.configure(baud, parity)
    for attempt in range(1, 4):
        try:
            actual_id = read_word(bus, sid, 0)
            actual_baud = CODE_BAUDS.get(read_word(bus, sid, 1))
            if (actual_id, actual_baud) != (sid, baud):
                raise CommunicationError(f"registros nao conferem: ID={actual_id}, baud={actual_baud}")
            LOG.info("VERIFICADO: registro ID=%d, baud=%d, comunicacao 8%s1", actual_id, actual_baud, parity)
            return pressure(bus, sid) if check_measurement else None
        except CommunicationError as error:
            LOG.warning("VERIFICACAO tentativa %d/3: %s", attempt, error)
            time.sleep(0.2)
    raise CommunicationError(f"Nao foi possivel verificar ID={sid}, {baud} 8{parity}1")


def write_once(bus, sid, address, values):
    LOG.warning("ESCREVENDO FC06 ID=%d endereco=0x%04X registros=%s", sid, address, values)
    try:
        bus.write(sid, address, values)
    except ModbusError:
        raise
    except CommunicationError as error:
        LOG.warning("Sem confirmacao utilizavel (%s). Nao repetirei a escrita; verificarei por leitura.", error)
    time.sleep(0.5)


def configure_device(bus, found, new_id=1):
    if not 1 <= new_id <= 247:
        raise ValueError("Novo ID deve ser 1..247")
    if len(found) != 1:
        raise CommunicationError(f"Esperava exatamente um dispositivo/configuracao, encontrei {len(found)}. Nenhuma escrita efetuada.")
    baud, parity, sid = found[0]
    if parity != "N":
        raise CommunicationError("O manual define 8N1 fixo; resposta em outra paridade exige revisao. Nenhuma escrita efetuada.")
    verify(bus, baud, sid)
    if sid != new_id:
        write_once(bus, sid, 0, new_id)
        verify(bus, baud, new_id)
        sid = new_id
    if baud != 9600:
        write_once(bus, sid, 1, 3)
    value = verify(bus, 9600, new_id)
    LOG.info("SUCESSO: ID=%d, 9600 8N1; medicao=%s", new_id, value)
    LOG.info("Persistencia apos desligamento nao testada; nenhum comando de salvar/reiniciar documentado foi enviado.")
    return value


def recover_read_only(bus, found, new_id=1):
    LOG.warning("DIAGNOSTICO somente leitura: procurando estado original/intermediario/final; nenhuma nova escrita.")
    candidates = list(dict.fromkeys([(9600, new_id)] + [(b, i) for b, _, sid in found for i in (new_id, sid)]))
    for baud, sid in candidates:
        bus.configure(baud, "N")
        try:
            value = pressure(bus, sid)
            LOG.warning("DISPOSITIVO AINDA RESPONDE: ID=%d %d 8N1 pressao=%s", sid, baud, value)
        except CommunicationError as error:
            LOG.info("DIAGNOSTICO ID=%d %d: %s", sid, baud, error)


def main():
    parser = argparse.ArgumentParser(description="Scanner ANHUI e configuracao de ID / 9600 8N1 com log em tempo real.")
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--new-id", type=int, default=2, help="Slave ID de destino: 1..247 (padrao 2)")
    parser.add_argument("--baudrates", type=int, nargs="+", default=list(BAUDRATES))
    parser.add_argument("--parities", choices=("N",), nargs="+", default=["N"])
    parser.add_argument("--max-id", type=int, default=247, help="IDs 1..N (maximo 247; padrao 247)")
    parser.add_argument("--timeout", type=float, default=0.4, help="segundos por consulta")
    parser.add_argument("--scan-only", action="store_true", help="nao escrever; apenas descobrir")
    parser.add_argument("--log", help="caminho do arquivo de log")
    if len(sys.argv) == 1:
        parser.print_help()
        return 2
    args = parser.parse_args()
    if not 1 <= args.new_id <= 247:
        parser.error("--new-id deve ser 1..247")
    if not 1 <= args.max_id <= 247 or args.timeout <= 0:
        parser.error("--max-id deve ser 1..247 e --timeout deve ser positivo")
    if any(b not in BAUDRATES for b in args.baudrates):
        parser.error(f"Baudrates documentados: {BAUDRATES}")
    app_dir = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    log_path = Path(args.log) if args.log else app_dir / "logs" / f"anhui-{datetime.now():%Y%m%d-%H%M%S}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_path, encoding="utf-8")])
    LOG.info("LOG: %s", log_path.resolve())
    LOG.info("MODO: %s", "SOMENTE LEITURA" if args.scan_only else "SCAN + ALTERAR ID/BAUD + VERIFICAR")
    LOG.info("Feche Modbus Poll e outros programas que utilizem %s; conecte apenas o transdutor a configurar.", args.port)
    LOG.info("IDs 1..%d; baudrates=%s; paridades=%s; timeout=%.3fs", args.max_id, args.baudrates, args.parities, args.timeout)
    bus = None
    found = []
    configuration_started = False
    try:
        bus = Bus(args.port, args.timeout)
        scan_ids = sorted(set(range(1, args.max_id + 1)) | {args.new_id})
        found = scan(bus, list(dict.fromkeys(args.baudrates)), list(dict.fromkeys(args.parities)), scan_ids)
        if args.scan_only:
            return 0 if found else 1
        configuration_started = len(found) == 1 and found[0][1] == "N"
        configure_device(bus, found, args.new_id)
        return 0
    except KeyboardInterrupt:
        LOG.warning("Interrompido. Se houve escrita, o dispositivo pode estar no estado intermediario; consulte o log.")
        return 130
    except (CommunicationError, serial.SerialException, OSError) as error:
        LOG.error("FALHA: %s", error)
        if bus is not None and configuration_started:
            try:
                recover_read_only(bus, found, args.new_id)
            except (CommunicationError, serial.SerialException, OSError) as recovery_error:
                LOG.error("Diagnostico indisponivel: %s", recovery_error)
        return 1
    finally:
        if bus is not None:
            bus.close()
            LOG.info("Porta fechada.")


if __name__ == "__main__":
    raise SystemExit(main())
