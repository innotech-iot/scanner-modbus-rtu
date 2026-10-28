"""Native Windows interface for the documented ANHUI Modbus RTU protocol."""
import logging
import os
from pathlib import Path
import queue
import sys
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from serial.tools import list_ports
import anhui_gui_core as core
import configure_anhui_transducer as protocol


class QueueLog(logging.Handler):
    def __init__(self, events):
        super().__init__()
        self.events = events
        self.setFormatter(logging.Formatter('%(asctime)s  %(message)s', '%H:%M:%S'))
    def emit(self, record):
        self.events.put(('log', self.format(record)))


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = None
        self.found = []
        self.detected_port = None
        self.closing = False
        self.worker = None
        self.handlers = []
        self.port = tk.StringVar()
        self.new_id = tk.StringVar(value='2')
        self.new_baud = tk.StringVar(value='9600')
        self.max_id = tk.StringVar(value='247')
        self.scan_baud = tk.StringVar(value='Todas (automatico)')
        self.status = tk.StringVar(value='Selecione a porta COM e clique em Detectar sensor.')
        self.detected = tk.StringVar(value='Nenhum sensor detectado')
        self.progress_text = tk.StringVar(value='A busca apenas le os dados do sensor.')
        root.title('ANHUI | Configurador Modbus RTU')
        root.geometry('900x760')
        root.minsize(820, 700)
        style = ttk.Style(root)
        if 'vista' in style.theme_names(): style.theme_use('vista')
        style.configure('Title.TLabel', font=('Segoe UI', 22, 'bold'))
        style.configure('Subtitle.TLabel', font=('Segoe UI', 10))
        style.configure('Result.TLabel', font=('Segoe UI', 13, 'bold'))
        style.configure('TButton', padding=(12, 7))
        outer = ttk.Frame(root, padding=24)
        outer.pack(fill='both', expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(5, weight=1)
        ttk.Label(outer, text='ANHUI', style='Title.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(outer, text='Detecte e configure seu transdutor de pressao / nivel por RS485.',
                  style='Subtitle.TLabel').grid(row=1, column=0, sticky='w', pady=(2,12))
        ttk.Label(outer, text='Conecte somente um transdutor. Feche outros programas que usem a mesma porta.',
                  foreground='#8a4800').grid(row=2, column=0, sticky='w', pady=(0,14))

        source = ttk.LabelFrame(outer, text='1. Conexao e descoberta', padding=14)
        source.grid(row=3, column=0, sticky='ew')
        source.columnconfigure(1, weight=1)
        ttk.Label(source, text='Porta COM').grid(row=0, column=0, sticky='w', padx=(0,12))
        self.port_box = ttk.Combobox(source, textvariable=self.port, state='readonly', width=18)
        self.port_box.grid(row=0, column=1, sticky='ew')
        self.refresh_button = ttk.Button(source, text='Atualizar portas', command=self.refresh_ports)
        self.refresh_button.grid(row=0, column=2, padx=(12,0))
        self.port_description = ttk.Label(source, text='')
        self.port_description.grid(row=1, column=0, columnspan=3, sticky='w', pady=(4,10))
        search = ttk.Frame(source)
        search.grid(row=2, column=0, columnspan=3, sticky='ew')
        ttk.Label(search, text='Buscar IDs de 1 ate').pack(side='left')
        self.range_box = ttk.Spinbox(search, from_=1, to=247, width=5, textvariable=self.max_id)
        self.range_box.pack(side='left', padx=(8,18))
        ttk.Label(search, text='Velocidade atual').pack(side='left')
        self.scan_box = ttk.Combobox(search, textvariable=self.scan_baud, state='readonly', width=20,
             values=['Todas (automatico)'] + [str(b) for b in sorted(protocol.BAUD_CODES)])
        self.scan_box.pack(side='left', padx=(8,0))
        actions = ttk.Frame(source)
        actions.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(12,8))
        self.detect_button = ttk.Button(actions, text='Detectar sensor', command=self.detect)
        self.detect_button.pack(side='left')
        self.stop_button = ttk.Button(actions, text='Parar busca', command=self.stop, state='disabled')
        self.stop_button.pack(side='left', padx=8)
        ttk.Label(actions, text='Formato fixo: 8N1', foreground='#555555').pack(side='right')
        self.progress = ttk.Progressbar(source, maximum=100)
        self.progress.grid(row=4, column=0, columnspan=3, sticky='ew')
        ttk.Label(source, textvariable=self.progress_text).grid(row=5, column=0, columnspan=3, sticky='w', pady=(5,0))

        target = ttk.LabelFrame(outer, text='2. Configuracao do sensor detectado', padding=14)
        target.grid(row=4, column=0, sticky='ew', pady=14)
        ttk.Label(target, textvariable=self.detected, style='Result.TLabel').pack(anchor='w', pady=(0,12))
        fields = ttk.Frame(target)
        fields.pack(fill='x')
        ttk.Label(fields, text='Novo Slave ID').pack(side='left')
        self.id_box = ttk.Spinbox(fields, from_=1, to=247, width=6, textvariable=self.new_id)
        self.id_box.pack(side='left', padx=(8,22))
        ttk.Label(fields, text='Novo baudrate').pack(side='left')
        self.baud_box = ttk.Combobox(fields, textvariable=self.new_baud, state='readonly', width=10,
                                    values=[str(b) for b in sorted(protocol.BAUD_CODES)])
        self.baud_box.pack(side='left', padx=(8,20))
        self.apply_button = ttk.Button(fields, text='Aplicar e verificar', command=self.apply, state='disabled')
        self.apply_button.pack(side='right')

        details = ttk.LabelFrame(outer, text='Andamento e resultado', padding=10)
        details.grid(row=5, column=0, sticky='nsew')
        ttk.Label(details, textvariable=self.status, wraplength=780).pack(anchor='w', pady=(0,8))
        self.log_view = ScrolledText(details, height=8, font=('Consolas',9), state='disabled', wrap='word',
                                    relief='flat', background='#f4f6f8')
        self.log_view.pack(fill='both', expand=True)
        self.log_label = ttk.Label(outer, text='', foreground='#555555', wraplength=820)
        self.log_label.grid(row=6, column=0, sticky='w', pady=(8,0))
        self.port.trace_add('write', self.port_changed)
        self.setup_logging()
        self.refresh_ports()
        self.poll_id = root.after(80, self.poll)
        root.protocol('WM_DELETE_WINDOW', self.on_close)

    def setup_logging(self):
        app_dir = Path(sys.executable if getattr(sys,'frozen',False) else __file__).resolve().parent
        qh = QueueLog(self.events)
        self.handlers.append(qh)
        protocol.LOG.addHandler(qh)
        protocol.LOG.setLevel(logging.INFO)
        for folder in (app_dir/'logs', Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'AnhuiConfigurador'/'logs'):
            try:
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f'anhui-gui-{datetime.now():%Y%m%d-%H%M%S-%f}.log'
                fh = logging.FileHandler(path, encoding='utf-8')
                fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
                protocol.LOG.addHandler(fh)
                self.handlers.append(fh)
                self.log_label.configure(text=f'Log: {path}')
                break
            except OSError:
                self.log_label.configure(text='Log em arquivo indisponivel; acompanhe o resultado nesta janela.')
        protocol.LOG.info('Pronto. Detectar e somente leitura; Aplicar altera ID e baudrate.')

    def port_changed(self, *_):
        self.found = []
        self.detected_port = None
        self.detected.set('Nenhum sensor detectado')
        self.apply_button.configure(state='disabled')
        self.port_description.configure(text=self.port_descriptions.get(self.port.get(), ''))

    def refresh_ports(self):
        if self.busy: return
        self.found = []
        self.detected_port = None
        self.detected.set('Nenhum sensor detectado')
        self.apply_button.configure(state='disabled')
        try:
            ports = list(list_ports.comports())
            self.port_descriptions = {p.device: p.description for p in ports}
            names = [p.device for p in ports]
            self.port_box.configure(values=names)
            if self.port.get() not in names: self.port.set(names[0] if names else '')
            self.port_description.configure(text=self.port_descriptions.get(self.port.get(),''))
            if not names: self.status.set('Nenhuma porta COM encontrada. Conecte o conversor USB/RS485 e atualize as portas.')
        except Exception as error:
            self.port_descriptions = {}
            self.status.set(f'Nao foi possivel listar as portas: {error}')

    def set_busy(self, mode):
        self.busy = mode
        for widget in (self.refresh_button,self.detect_button,self.range_box,self.id_box):
            widget.configure(state='disabled' if mode else 'normal')
        for widget in (self.port_box,self.scan_box,self.baud_box):
            widget.configure(state='disabled' if mode else 'readonly')
        self.stop_button.configure(state='normal' if mode=='scan' else 'disabled')
        self.apply_button.configure(state='normal' if not mode and len(self.found)==1 and self.detected_port==self.port.get() else 'disabled')

    def detect(self):
        if self.busy: return
        try:
            port = self.port.get().strip()
            maximum = int(self.max_id.get())
            if not port: raise ValueError('Selecione uma porta COM.')
            if not 1 <= maximum <= 247: raise ValueError('A faixa de busca deve ser de 1 a 247.')
            bauds = list(protocol.BAUDRATES) if self.scan_baud.get().startswith('Todas') else [int(self.scan_baud.get())]
        except ValueError as error:
            self.status.set(str(error)); return
        self.found = []
        self.detected_port = None
        self.detected.set('Buscando sensor...')
        self.cancel.clear()
        self.progress.configure(value=0)
        self.status.set('Busca em andamento. A busca completa pode levar 14 minutos ou mais.')
        self.set_busy('scan')
        self.start_worker('scan', port, bauds, maximum)

    def apply(self):
        if self.busy or len(self.found)!=1 or self.detected_port!=self.port.get(): return
        try:
            sid, baud = int(self.new_id.get()), int(self.new_baud.get())
            if not 1 <= sid <= 247: raise ValueError('Novo Slave ID deve ser de 1 a 247.')
            if baud not in protocol.BAUD_CODES: raise ValueError('Selecione um baudrate da lista.')
        except ValueError as error:
            self.status.set(str(error)); return
        snapshot = list(self.found)
        self.found = []
        self.status.set(f'Aplicando ID {sid} / {baud} baud. Aguarde a verificacao; mantenha o sensor conectado.')
        self.set_busy('apply')
        self.start_worker('apply', self.port.get(), snapshot, sid, baud)

    def start_worker(self, mode, port, *args):
        def run():
            bus = None
            try:
                bus = core.Bus(port, 0.4)
                if mode=='scan':
                    bauds, maximum = args
                    found = core.discover(bus, bauds, range(1,maximum+1), self.cancel,
                        lambda *data:self.events.put(('progress',data)))
                    self.events.put(('scan_done',(port,found)))
                else:
                    found, sid, baud = args
                    value = core.configure(bus, found, sid, baud)
                    self.events.put(('apply_done',(port,sid,baud,value)))
            except core.ScanCancelled:
                self.events.put(('cancelled',None))
            except Exception as error:
                protocol.LOG.exception('FALHA: %s',error)
                if mode=='apply' and bus is not None:
                    try: core.diagnose(bus,*args)
                    except Exception as diagnostic: protocol.LOG.warning('Diagnostico indisponivel: %s',diagnostic)
                self.events.put(('error',str(error)))
            finally:
                if bus is not None:
                    try: bus.close()
                    except Exception: pass
                self.events.put(('idle',None))
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def stop(self):
        if self.busy=='scan':
            self.cancel.set()
            self.stop_button.configure(state='disabled')
            self.status.set('Parando a busca apos a consulta atual...')

    def poll(self):
        for _ in range(400):
            try: kind, data = self.events.get_nowait()
            except queue.Empty: break
            if kind=='log':
                self.log_view.configure(state='normal')
                self.log_view.insert('end',data+'\n')
                lines = int(self.log_view.index('end-1c').split('.')[0])
                if lines>600: self.log_view.delete('1.0',f'{lines-500}.0')
                self.log_view.see('end'); self.log_view.configure(state='disabled')
            elif kind=='progress':
                done,total,baud,sid,found=data
                self.progress.configure(value=100*done/total)
                self.progress_text.set(f'{done} / {total} consultas | ID {sid} | {baud} baud | {len(found)} resposta(s) valida(s)')
                if found: self.detected.set('Encontrado: '+ '  |  '.join(f'ID {s} / {b} baud' for b,_,s in found))
            elif kind=='scan_done':
                port, self.found = data
                self.detected_port = port
                if len(self.found)==1:
                    baud,_,sid=self.found[0]
                    self.detected.set(f'{port}   |   ID {sid}   |   {baud} baud   |   8N1')
                    self.status.set('Sensor detectado. Escolha o novo ID e baudrate e clique em Aplicar e verificar.')
                elif not self.found:
                    self.detected.set('Nenhum sensor detectado')
                    self.status.set('Sem resposta valida. Confira porta, alimentacao e fios A/B; tente a busca completa.')
                else:
                    self.detected.set(f'{len(self.found)} configuracoes encontradas; aplicacao bloqueada')
                    self.status.set('Conecte somente um transdutor e detecte novamente.')
            elif kind=='apply_done':
                port,sid,baud,value=data
                self.found=[(baud,'N',sid)]; self.detected_port=port
                self.detected.set(f'{port}   |   ID {sid}   |   {baud} baud   |   8N1')
                self.status.set(f'Sucesso: ID {sid} e {baud} baud confirmados por leitura. Persistencia apos desligamento ainda nao testada.')
            elif kind in ('error','cancelled'):
                self.found=[]; self.detected_port=None
                self.detected.set('Detecte novamente antes de configurar')
                self.status.set('Busca interrompida. Nenhuma configuracao foi escrita.' if kind=='cancelled' else f'Falha: {data}. Consulte o log; uma alteracao pode ter ficado parcial se houve escrita.')
            elif kind=='idle':
                self.set_busy(None)
                if self.closing:
                    self.destroy(); return
        self.poll_id = self.root.after(80,self.poll)

    def on_close(self):
        if self.busy=='apply':
            self.status.set('Aguarde a configuracao e verificacao terminarem antes de fechar a janela.')
        elif self.busy=='scan':
            self.closing=True; self.stop()
        else: self.destroy()

    def destroy(self):
        try: self.root.after_cancel(self.poll_id)
        except tk.TclError: pass
        for handler in self.handlers:
            protocol.LOG.removeHandler(handler); handler.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__=='__main__':
    main()
