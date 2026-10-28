"""ANHUI GUI operations; register map comes from the supplied PDF, pp. 1-3."""
import configure_anhui_transducer as p


class ScanCancelled(Exception):
    pass


class Bus(p.Bus):
    def write(self, sid, address, value):
        if not ((address == 0 and 1 <= value <= 247) or
                (address == 1 and value in p.CODE_BAUDS)):
            raise ValueError('ID ou codigo de baudrate fora do manual/faixa permitida')
        payload = p.write_payload(sid, address, value)
        response = self.exchange(payload)
        if response[:-2] != payload:
            raise p.CommunicationError('Confirmacao de escrita inesperada')


def discover(bus, baudrates, ids, cancel, progress):
    ids = tuple(ids)
    baudrates = tuple(baudrates)
    found = []
    total = len(ids) * len(baudrates)
    done = 0
    for baud in baudrates:
        if cancel.is_set():
            raise ScanCancelled()
        bus.configure(baud, 'N')
        for sid in ids:
            if cancel.is_set():
                raise ScanCancelled()
            p.LOG.info('BUSCA %d 8N1 ID=%d', baud, sid)
            try:
                actual_id = p.read_word(bus, sid, 0)
                code = p.read_word(bus, sid, 1)
                if actual_id != sid or p.CODE_BAUDS.get(code) != baud:
                    raise p.CommunicationError('ID/baud lidos nao conferem')
                p.pressure(bus, sid)
            except p.CommunicationError as error:
                p.LOG.debug('Sem resposta valida: %s', error)
            else:
                found.append((baud, 'N', sid))
                p.LOG.info('ENCONTRADO ID=%d | %d baud | 8N1', sid, baud)
            done += 1
            progress(done, total, baud, sid, tuple(found))
    if cancel.is_set():
        raise ScanCancelled()
    return found


def configure(bus, found, new_id, new_baud):
    if not 1 <= new_id <= 247:
        raise ValueError('Slave ID deve ser de 1 a 247')
    if new_baud not in p.BAUD_CODES:
        raise ValueError('Baudrate nao documentado no manual')
    if len(found) != 1:
        raise p.CommunicationError('E necessario detectar exatamente um sensor antes de configurar')
    baud, parity, sid = found[0]
    if parity != 'N':
        raise p.CommunicationError('O manual exige 8N1')
    p.verify(bus, baud, sid)
    if sid != new_id:
        p.write_once(bus, sid, 0, new_id)
        p.verify(bus, baud, new_id)
        sid = new_id
    if baud != new_baud:
        p.write_once(bus, sid, 1, p.BAUD_CODES[new_baud])
    value = p.verify(bus, new_baud, new_id)
    p.LOG.info('SUCESSO: ID=%d | %d baud | 8N1 | medicao=%s', new_id, new_baud, value)
    return value


def diagnose(bus, found, new_id, new_baud):
    """Read only after an uncertain write; never retry the write."""
    candidates = list(dict.fromkeys([(new_baud, new_id)] +
        [(baud, sid) for baud, _, old_id in found for sid in (new_id, old_id)]))
    for baud, sid in candidates:
        try:
            p.verify(bus, baud, sid)
            p.LOG.warning('DIAGNOSTICO: sensor responde em ID=%d / %d baud', sid, baud)
        except p.CommunicationError:
            pass
