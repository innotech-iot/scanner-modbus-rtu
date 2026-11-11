"""Innotech Scanner de Slave Perdido: generic read-only RTU exploration."""
import csv
import logging
import os
from pathlib import Path
import queue
import sys
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog
from serial import SerialException
from serial.tools import list_ports
import scanner_core as scan
import anhui_gui_core as vendor
import configure_anhui_transducer as protocol
from anhui_gui import QueueLog

INK = '#10282D'
TEAL = '#0B6963'
MUTED = '#486263'
SURFACE = '#FFFFFF'
BACKGROUND = '#EFF5F3'
BORDER = '#CCDCD7'
SELECTION = '#D9EFEB'

SCAN_PARITIES = {'8N1': ('N',), '8E1': ('E',), '8O1': ('O',), 'Todas': ('N', 'E', 'O')}

GENERIC='Somente leitura — genérico'
PROFILE='Anhui — manual atualizado'
LEGACY_PROFILE='Anhui — manual anterior'

def assets():
    return Path(__file__).resolve().parent/'scanner_assets'

class App:
    def __init__(self,root):
        self.root=root;self.events=queue.Queue();self.cancel=threading.Event()
        self.busy=None;self.worker=None;self.closing=False;self.handlers=[]
        self.found=[];self.endpoint=None;self.rows=[];self.port_descriptions={};self.usb_ports=[];self.bluetooth_ports=set()
        self.port=tk.StringVar();self.max_id=tk.StringVar(value='247')
        self.scan_baud=tk.StringVar(value='Todas (automático)')
        self.scan_parity=tk.StringVar(value='8N1')
        self.float_result=tk.StringVar()
        self.probe_fc=tk.StringVar(value='03 — Holding Registers');self.probe_address=tk.StringVar(value='0')
        self.new_id=tk.StringVar(value='2');self.new_baud=tk.StringVar(value='9600');self.profile=tk.StringVar(value=GENERIC)
        self.start_address=tk.StringVar(value='0');self.end_address=tk.StringVar(value='100')
        self.nonzero=tk.BooleanVar(value=False)
        self.tables={fc:tk.BooleanVar(value=fc in (3,4)) for fc in scan.TABLES}
        self.status=tk.StringVar(value='1. Selecione a porta COM.  2. Encontre o dispositivo.  3. Explore as tabelas.')
        self.selection_text=tk.StringVar(value='Nenhum dispositivo selecionado')
        self.progress_text=tk.StringVar(value='Pronto • abertura da janela não acessa o dispositivo')
        root.title('Innotech | Scanner de Slave Perdido')
        root.geometry('1060x820');root.minsize(960,740)
        self.icon=tk.PhotoImage(file=str(assets()/'innotech.png'))
        root.iconphoto(True,self.icon);root.iconbitmap(str(assets()/'innotech.ico'))
        self.logo = tk.PhotoImage(file=str(assets() / 'innotech_horizontal.png'))
        self.configure_style()
        header = tk.Frame(root, bg=SURFACE, padx=24, pady=17)
        header.pack(fill='x')
        tk.Label(header, image=self.logo, bg=SURFACE, borderwidth=0).pack(side='left')
        tk.Label(
            header, text='Scanner de Slave Perdido', bg=SURFACE, fg=INK,
            font=('Segoe UI', 20, 'bold'), borderwidth=0,
        ).pack(side='right')
        tk.Frame(root, bg=TEAL, height=3).pack(fill='x')
        outer=ttk.Frame(root,padding=(20,12));outer.pack(fill='both',expand=True)
        outer.columnconfigure(0,weight=1);outer.rowconfigure(2,weight=1)
        connect=ttk.Frame(outer);connect.grid(row=0,column=0,sticky='ew',pady=(0,4))
        ttk.Label(connect,text='Porta COM').pack(side='left',padx=(0,8))
        self.port_box=ttk.Combobox(connect,textvariable=self.port,state='readonly',width=14)
        self.port_box.pack(side='left')
        self.refresh_button=ttk.Button(connect,text='Atualizar portas',command=self.refresh_ports)
        self.refresh_button.pack(side='left',padx=8)
        self.port_description=ttk.Label(connect,text='',foreground=MUTED)
        self.port_description.pack(side='left',padx=8)
        ttk.Label(connect,text='RS485 / Modbus RTU',foreground=MUTED).pack(side='right')
        ttk.Label(outer,textvariable=self.selection_text,style='Badge.TLabel').grid(row=1,column=0,sticky='w',pady=(5,10))
        self.notebook=ttk.Notebook(outer);self.notebook.grid(row=2,column=0,sticky='nsew')
        self.find_tab=ttk.Frame(self.notebook,padding=16)
        self.read_tab=ttk.Frame(self.notebook,padding=16)
        self.config_tab=ttk.Frame(self.notebook,padding=16)
        for tab,label in [(self.find_tab,'Encontrar dispositivo'),(self.read_tab,'Explorar tabelas'),(self.config_tab,'Configurar transdutor')]:self.notebook.add(tab,text=label)
        self.inputs=[];self.readonly=[]
        self.build_find();self.build_reader();self.build_config()
        footer=ttk.Frame(outer);footer.grid(row=3,column=0,sticky='ew',pady=(10,0))
        footer.columnconfigure(0,weight=1)
        self.progress=ttk.Progressbar(footer,maximum=100);self.progress.grid(row=0,column=0,sticky='ew',padx=(0,10))
        self.stop_button=ttk.Button(footer,text='Parar busca',command=self.stop,state='disabled');self.stop_button.grid(row=0,column=1)
        ttk.Label(footer,textvariable=self.progress_text).grid(row=1,column=0,columnspan=2,sticky='w',pady=4)
        ttk.Label(outer,textvariable=self.status,wraplength=980).grid(row=4,column=0,sticky='ew',pady=(2,6))
        self.log_view=tk.Text(outer,height=3,font=('Consolas',9),bg=SURFACE,fg=INK,padx=8,pady=5,relief='flat',wrap='word',state='disabled')
        self.log_view.grid(row=5,column=0,sticky='ew')
        self.log_label=ttk.Label(outer,text='',foreground=MUTED,wraplength=990)
        self.log_label.grid(row=6,column=0,sticky='w',pady=(4,0))
        self.port.trace_add('write',self.port_changed)
        self.profile.trace_add('write',lambda *_:self.update_controls())
        self.nonzero.trace_add('write',lambda *_:self.render_rows())
        self.setup_logging();self.refresh_ports();self.update_controls()
        self.poll_id=root.after(80,self.poll);root.protocol('WM_DELETE_WINDOW',self.on_close)

    def configure_style(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=BACKGROUND, foreground=INK, font=('Segoe UI', 10))
        style.configure('TButton', padding=(11, 6), bordercolor=BORDER,
                        lightcolor=BACKGROUND, darkcolor=BACKGROUND)
        style.map('TButton', background=[('active', SELECTION)])
        style.configure('Primary.TButton', background=TEAL, foreground=SURFACE,
                        lightcolor=TEAL, darkcolor=TEAL, bordercolor=TEAL)
        style.map('Primary.TButton',
                  background=[('disabled', BORDER), ('active', '#08544F')],
                  foreground=[('disabled', MUTED)])
        style.configure('TNotebook', bordercolor=BORDER, tabmargins=(0, 0, 0, 0))
        style.configure('TNotebook.Tab', padding=(16, 9), bordercolor=BORDER)
        style.map('TNotebook.Tab', background=[('selected', SELECTION)],
                  foreground=[('selected', TEAL)])
        style.configure('Treeview', background=SURFACE, fieldbackground=SURFACE, rowheight=27)
        style.configure('Treeview.Heading', background=SELECTION,
                        font=('Segoe UI', 9, 'bold'))
        style.map('Treeview', background=[('selected', TEAL)],
                  foreground=[('selected', SURFACE)])
        style.configure('Badge.TLabel', foreground=TEAL, font=('Segoe UI', 11, 'bold'))
        style.configure('Horizontal.TProgressbar', background=TEAL, troughcolor=BORDER,
                        lightcolor=TEAL, darkcolor=TEAL, bordercolor=BORDER)

    def build_find(self):
        tab=self.find_tab;tab.columnconfigure(0,weight=1);tab.rowconfigure(3,weight=1)
        ttk.Label(tab,text='Busca somente leitura. Uma exceção Modbus válida também confirma que o ID está respondendo.',wraplength=930).grid(row=0,column=0,sticky='w')
        opts=ttk.Frame(tab);opts.grid(row=1,column=0,sticky='ew',pady=12)
        ttk.Label(opts,text='IDs de 1 até').pack(side='left')
        maximum=ttk.Spinbox(opts,from_=1,to=247,textvariable=self.max_id,width=5);maximum.pack(side='left',padx=(8,18));self.inputs.append(maximum)
        ttk.Label(opts,text='Baudrate atual').pack(side='left')
        speeds=ttk.Combobox(opts,textvariable=self.scan_baud,state='readonly',width=19,values=['Todas (automático)']+[str(b) for b in sorted(protocol.BAUD_CODES)])
        speeds.pack(side='left',padx=(8,18));self.readonly.append(speeds)
        ttk.Label(opts,text='Paridade').pack(side='left')
        parities=ttk.Combobox(opts,textvariable=self.scan_parity,state='readonly',width=8,values=list(SCAN_PARITIES))
        parities.pack(side='left',padx=8);self.readonly.append(parities)
        self.detect_button=ttk.Button(opts,text='Encontrar dispositivos',command=self.detect,style='Primary.TButton');self.detect_button.pack(side='right')
        probe=ttk.Frame(tab);probe.grid(row=2,column=0,sticky='ew',pady=(0,12))
        ttk.Label(probe,text='Consulta de descoberta').pack(side='left')
        fc=ttk.Combobox(probe,textvariable=self.probe_fc,state='readonly',width=27,values=[f'{i:02d} — {scan.TABLES[i]}' for i in (3,4,1,2)])
        fc.pack(side='left',padx=8);self.readonly.append(fc)
        ttk.Label(probe,text='Endereço (offset)').pack(side='left',padx=(8,0))
        addr=ttk.Entry(probe,textvariable=self.probe_address,width=8);addr.pack(side='left',padx=8);self.inputs.append(addr)
        results=ttk.Frame(tab);results.grid(row=3,column=0,sticky='nsew');results.columnconfigure(0,weight=1);results.rowconfigure(0,weight=1)
        self.devices=ttk.Treeview(results,columns=('id','baud','format'),show='headings',height=5,selectmode='browse')
        for key,label,width in [('id','Slave ID',160),('baud','Baudrate',200),('format','Comunicação',300)]:
            self.devices.heading(key,text=label);self.devices.column(key,width=width,anchor='center')
        self.devices.grid(row=0,column=0,sticky='nsew')
        scroll=ttk.Scrollbar(results,orient='vertical',command=self.devices.yview);scroll.grid(row=0,column=1,sticky='ns');self.devices.configure(yscrollcommand=scroll.set)
        self.devices.bind('<<TreeviewSelect>>',self.select_device)
        bottom=ttk.Frame(tab);bottom.grid(row=4,column=0,sticky='ew',pady=(10,0))
        ttk.Label(bottom,text='Selecione uma linha para explorar. Conecte apenas um dispositivo se for alterar a configuração.',wraplength=730).pack(side='left')
        self.go_button=ttk.Button(bottom,text='Explorar selecionado →',command=lambda:self.notebook.select(self.read_tab));self.go_button.pack(side='right')

    def build_reader(self):
        tab=self.read_tab;tab.columnconfigure(0,weight=1);tab.rowconfigure(4,weight=1)
        checks=ttk.Frame(tab);checks.grid(row=0,column=0,sticky='ew')
        for fc in (3,4,1,2):
            check=ttk.Checkbutton(checks,text=f'FC{fc:02d} {scan.TABLES[fc]}',variable=self.tables[fc]);check.pack(side='left',padx=(0,12));self.inputs.append(check)
        controls=ttk.Frame(tab);controls.grid(row=1,column=0,sticky='ew',pady=10)
        for text,var in [('Endereço inicial',self.start_address),('Final',self.end_address)]:
            ttk.Label(controls,text=text).pack(side='left')
            entry=ttk.Entry(controls,textvariable=var,width=8);entry.pack(side='left',padx=(8,16));self.inputs.append(entry)
        ttk.Checkbutton(controls,text='Só valores ≠ 0',variable=self.nonzero).pack(side='left')
        self.export_button=ttk.Button(controls,text='Exportar CSV',command=self.export);self.export_button.pack(side='right')
        self.explore_button=ttk.Button(controls,text='Buscar registradores',command=self.explore);self.explore_button.pack(side='right',padx=8)
        ttk.Label(tab,text='Offsets de 0 a 65535 (decimal ou 0xHEX). Zero é válido. Valores brutos: o significado e a escala vêm do manual.',foreground=MUTED,wraplength=930).grid(row=2,column=0,sticky='w',pady=(0,8))
        float_controls=ttk.Frame(tab);float_controls.grid(row=3,column=0,sticky='ew',pady=(0,8))
        self.float_button=ttk.Button(float_controls,text='Ler Float32 Anhui',command=self.read_float)
        self.float_button.pack(side='left')
        ttk.Label(float_controls,textvariable=self.float_result).pack(side='left',padx=10)
        frame=ttk.Frame(tab);frame.grid(row=4,column=0,sticky='nsew');frame.columnconfigure(0,weight=1);frame.rowconfigure(0,weight=1)
        cols=('fc','address','reference','value','signed','hex')
        self.registers=ttk.Treeview(frame,columns=cols,show='headings',height=7)
        for key,label,width in [('fc','Tabela',190),('address','Offset',100),('reference','Ref. convencional',140),('value','UInt16 / bit',120),('signed','Int16',100),('hex','Hexadecimal',130)]:
            self.registers.heading(key,text=label);self.registers.column(key,width=width,anchor='center')
        self.registers.grid(row=0,column=0,sticky='nsew')
        scroll=ttk.Scrollbar(frame,orient='vertical',command=self.registers.yview);scroll.grid(row=0,column=1,sticky='ns');self.registers.configure(yscrollcommand=scroll.set)
        self.count_text=tk.StringVar(value='Nenhuma leitura ainda. A busca não escreve nas tabelas.')
        ttk.Label(tab,textvariable=self.count_text).grid(row=5,column=0,sticky='w',pady=(8,0))

    def build_config(self):
        tab=self.config_tab
        ttk.Label(tab,text='Alteração de ID e baudrate exige o mapa correto do fabricante.',font=('Segoe UI',13,'bold')).pack(anchor='w',pady=(0,10))
        ttk.Label(tab,text='A descoberta genérica não identifica o fabricante. Use o perfil abaixo apenas no transdutor do manual incluído; outros equipamentos podem ser explorados em modo somente leitura.',wraplength=900).pack(anchor='w')
        profile=ttk.Combobox(tab,textvariable=self.profile,state='readonly',values=[GENERIC,PROFILE,LEGACY_PROFILE],width=57)
        profile.pack(anchor='w',pady=18);self.readonly.append(profile)
        fields=ttk.Frame(tab);fields.pack(fill='x')
        ttk.Label(fields,text='Novo Slave ID').pack(side='left')
        entry=ttk.Spinbox(fields,from_=1,to=247,textvariable=self.new_id,width=6);entry.pack(side='left',padx=(8,22));self.inputs.append(entry)
        ttk.Label(fields,text='Novo baudrate').pack(side='left')
        baud=ttk.Combobox(fields,textvariable=self.new_baud,state='readonly',values=[str(b) for b in sorted(protocol.BAUD_CODES)],width=11)
        baud.pack(side='left',padx=8);self.readonly.append(baud)
        self.apply_button=ttk.Button(fields,text='Aplicar e gravar',command=self.apply,style='Primary.TButton');self.apply_button.pack(side='left',padx=20)
        ttk.Label(tab,text='Conecte somente um transdutor. A configuração atual será verificada antes de qualquer escrita.\nO scanner confirma ID e baudrate antes de gravar a configuração no Anhui.',foreground=MUTED,wraplength=900).pack(anchor='w',pady=20)

    def setup_logging(self):
        handler=QueueLog(self.events);self.handlers.append(handler);protocol.LOG.addHandler(handler);protocol.LOG.setLevel(logging.INFO)
        base=Path(sys.executable if getattr(sys,'frozen',False) else __file__).resolve().parent
        for folder in [base/'logs',Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'InnotechScanner'/'logs']:
            try:
                folder.mkdir(parents=True,exist_ok=True);path=folder/f'scanner-{datetime.now():%Y%m%d-%H%M%S-%f}.log'
                fh=logging.FileHandler(path,encoding='utf-8');fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
                protocol.LOG.addHandler(fh);self.handlers.append(fh);self.log_label.configure(text=f'Log: {path}');break
            except OSError:self.log_label.configure(text='Log em arquivo indisponível.')
        protocol.LOG.info('Scanner pronto. Descoberta e exploração são somente leitura.')

    def refresh_ports(self):
        if self.busy:return
        self.invalidate()
        try:
            ports = list(list_ports.comports())
            self.bluetooth_ports = {
                port.device for port in ports
                if 'BTHENUM' in getattr(port, 'hwid', '').upper()
                or 'BLUETOOTH' in (port.description or '').upper()
            }
            self.usb_ports = [
                port.device for port in ports
                if getattr(port, 'vid', None) is not None
                or getattr(port, 'hwid', '').upper().startswith('USB')
            ]
            ports.sort(key=lambda port: (port.device not in self.usb_ports,
                                         port.device in self.bluetooth_ports))
            self.port_descriptions = {port.device: port.description for port in ports}
            names=list(self.port_descriptions);self.port_box.configure(values=names)
            if self.port.get() not in names:self.port.set(names[0] if names else '')
            self.port_description.configure(text=self.port_descriptions.get(self.port.get(),''))
            if not names or all(name in self.bluetooth_ports for name in names):
                self.status.set('Conecte o conversor USB/RS485 e clique em Atualizar portas.')
        except Exception as error:self.status.set(f'Falha ao listar portas: {error}')

    def invalidate(self):
        self.found=[];self.endpoint=None;self.rows=[]
        self.devices.delete(*self.devices.get_children());self.render_rows()
        self.float_result.set('')
        self.selection_text.set('Nenhum dispositivo selecionado');self.update_controls()

    def port_changed(self,*_):
        self.invalidate();self.port_description.configure(text=self.port_descriptions.get(self.port.get(),''))

    def update_controls(self):
        enabled=not self.busy
        for widget in self.inputs:widget.configure(state='normal' if enabled else 'disabled')
        for widget in self.readonly+[self.port_box]:widget.configure(state='readonly' if enabled else 'disabled')
        for widget in [self.detect_button,self.refresh_button]:widget.configure(state='normal' if enabled else 'disabled')
        for widget in [self.explore_button,self.go_button,self.float_button]:widget.configure(state='normal' if enabled and self.endpoint else 'disabled')
        self.export_button.configure(state='normal' if enabled and self.rows else 'disabled')
        self.apply_button.configure(state='normal' if enabled and self.endpoint and len(self.found)==1 and self.profile.get() in (PROFILE,LEGACY_PROFILE) else 'disabled')
        self.stop_button.configure(state='normal' if self.busy in ('scan','explore') else 'disabled')

    def select_device(self,*_):
        if self.busy:return
        selected=self.devices.selection()
        if not selected:return
        index=int(selected[0]);baud,parity,sid=self.found[index]
        endpoint=(self.port.get(),baud,parity,sid)
        if self.endpoint!=endpoint:self.rows=[];self.render_rows();self.float_result.set('')
        self.endpoint=endpoint
        self.selection_text.set(f'{self.port.get()}  •  Slave ID {sid}  •  {baud} baud  •  8{parity}1')
        self.update_controls()

    @staticmethod
    def number(text):
        text=text.strip();return int(text,16) if text.lower().startswith('0x') else int(text,10)

    def detect(self):
        if self.busy:return
        try:
            if not self.port.get():raise ValueError('Selecione a porta COM.')
            maximum=self.number(self.max_id.get());address=self.number(self.probe_address.get());fc=int(self.probe_fc.get()[:2])
            if not 1<=maximum<=247 or not 0<=address<=65535:raise ValueError('IDs: 1–247. Offset: 0–65535.')
            bauds=list(protocol.BAUDRATES) if self.scan_baud.get().startswith('Todas') else [int(self.scan_baud.get())]
        except ValueError as error:self.status.set(str(error));return
        parities=SCAN_PARITIES[self.scan_parity.get()]
        self.invalidate();self.busy='scan';self.cancel.clear();self.progress.configure(value=0);self.update_controls()
        self.status.set('Buscando IDs nas velocidades e paridades selecionadas.')
        self.worker=threading.Thread(target=self.run,args=('scan',(self.port.get(),bauds,maximum,fc,address,parities)),daemon=True);self.worker.start()

    def explore(self):
        if self.busy or not self.endpoint:return
        try:
            start,end=self.number(self.start_address.get()),self.number(self.end_address.get())
            tables=[fc for fc,var in self.tables.items() if var.get()]
            if not tables or not 0<=start<=end<=65535:raise ValueError('Selecione tabelas e uma faixa válida de 0 a 65535.')
        except ValueError as error:self.status.set(str(error));return
        self.rows=[];self.render_rows();self.busy='explore';self.cancel.clear();self.progress.configure(value=0);self.update_controls()
        self.status.set('Lendo endereços um a um para encontrar também mapas com lacunas. Nenhuma escrita é enviada.')
        self.worker=threading.Thread(target=self.run,args=('explore',(self.endpoint,tables,start,end)),daemon=True);self.worker.start()

    def read_float(self):
        if self.busy or not self.endpoint:
            return
        self.busy='float'
        self.float_result.set('')
        self.update_controls()
        self.status.set('Lendo a medição Float32 do Anhui...')
        self.worker=threading.Thread(target=self.run,args=('float',(self.endpoint,)),daemon=True)
        self.worker.start()

    def apply(self):
        if self.busy or not self.endpoint or len(self.found)!=1 or self.profile.get() not in (PROFILE,LEGACY_PROFILE):return
        try:
            sid=self.number(self.new_id.get());baud=int(self.new_baud.get())
            if not 1<=sid<=247 or baud not in protocol.BAUD_CODES:raise ValueError('Selecione ID 1–247 e uma velocidade da lista.')
        except ValueError as error:self.status.set(str(error));return
        self.busy='apply';self.update_controls();self.status.set(f'Aplicando ID {sid} / {baud} baud. Aguarde a confirmação da gravação.')
        self.worker=threading.Thread(target=self.run,args=('apply',(self.endpoint,sid,baud,self.profile.get()==PROFILE)),daemon=True);self.worker.start()

    def run(self,mode,args):
        bus=None
        try:
            if mode=='scan':
                port,bauds,maximum,fc,address,parities=args
                bus=scan.Bus(port,.4)
                found=scan.discover(bus,bauds,range(1,maximum+1),fc,address,self.cancel,lambda *data:self.events.put(('scan_progress',data)),parities=parities)
                self.events.put(('scan_done',found))
            else:
                endpoint,*rest=args;port,baud,parity,sid=endpoint
                bus=scan.Bus(port,.4);bus.configure(baud,parity)
                if mode=='explore':
                    tables,start,end=rest
                    summary=scan.explore(bus,sid,tables,start,end,self.cancel,
                        lambda row:self.events.put(('row',dict(row,port=port,slave_id=sid,baudrate=baud))),
                        lambda *data:self.events.put(('read_progress',data)))
                    self.events.put(('read_done',summary))
                elif mode=='float':
                    self.events.put(('float_done',scan.read_anhui_float32(bus,sid)))
                else:
                    target_id,target_baud,updated_manual=rest
                    vendor.configure(bus,[(baud,parity,sid)],target_id,target_baud,save=True,updated_manual=updated_manual)
                    self.events.put(('apply_done',(port,target_baud,parity,target_id)))
        except scan.Cancelled:self.events.put(('cancelled',mode))
        except Exception as error:
            if bus is None and isinstance(error, SerialException):
                port = args[0] if mode == 'scan' else args[0][0]
                protocol.LOG.warning('Falha ao abrir %s: %s', port, error)
                self.events.put(('port_error', port))
                return
            protocol.LOG.exception('Falha em %s: %s',mode,error)
            if mode=='apply' and bus is not None:
                endpoint,target_id,target_baud,_=args
                try:vendor.diagnose(bus,[(endpoint[1],endpoint[2],endpoint[3])],target_id,target_baud)
                except Exception as diagnostic:protocol.LOG.warning('Diagnóstico: %s',diagnostic)
            self.events.put(('error',(mode,str(error))))
        finally:
            if bus is not None:
                try:bus.close()
                except Exception:pass
            self.events.put(('idle',None))

    def row_values(self,row):
        return (f"FC{row['fc']:02d} {row['table']}",row['address'],row['reference'],row['value'],row['signed'] if row['fc'] in (3,4) else '—',row['hex'])

    def render_rows(self):
        if not hasattr(self,'registers'):return
        self.registers.delete(*self.registers.get_children())
        for row in self.rows:
            if not self.nonzero.get() or row['value']!=0:self.registers.insert('','end',values=self.row_values(row))
        self.count_text.set(f'{len(self.registers.get_children())} exibido(s) / {len(self.rows)} endereço(s) com leitura válida. Zero também é dado.')

    def export(self):
        if self.busy or not self.rows:return
        path=filedialog.asksaveasfilename(parent=self.root,title='Exportar todas as leituras válidas',defaultextension='.csv',filetypes=[('CSV','*.csv')],initialfile='Innotech_Registradores.csv')
        if not path:return
        columns=['port','slave_id','baudrate','fc','table','address','reference','value','signed','hex']
        try:
            with open(path,'w',encoding='utf-8-sig',newline='') as file:
                writer=csv.DictWriter(file,fieldnames=columns,delimiter=';');writer.writeheader();writer.writerows(self.rows)
            self.status.set(f'CSV salvo com todas as {len(self.rows)} leituras válidas, incluindo zeros: {path}')
        except OSError as error:self.status.set(f'Falha ao salvar CSV: {error}')

    def stop(self):
        if self.busy in ('scan','explore'):
            self.cancel.set();self.stop_button.configure(state='disabled');self.status.set('Parando após a consulta atual...')

    def poll(self):
        for _ in range(500):
            try:kind,data=self.events.get_nowait()
            except queue.Empty:break
            if kind=='log':
                self.log_view.configure(state='normal');self.log_view.insert('end',data+'\n')
                lines=int(self.log_view.index('end-1c').split('.')[0])
                if lines>150:self.log_view.delete('1.0',f'{lines-100}.0')
                self.log_view.see('end');self.log_view.configure(state='disabled')
            elif kind=='scan_progress':
                done,total,baud,sid,found=data;self.progress.configure(value=100*done/total)
                self.progress_text.set(f'{done}/{total} consultas • ID {sid} • {baud} baud • {len(found)} resposta(s) válida(s)')
            elif kind=='scan_done':
                self.found=list(data)
                for i,(baud,parity,sid) in enumerate(self.found):self.devices.insert('','end',iid=str(i),values=(sid,baud,f'8{parity}1 — resposta RTU válida'))
                self.status.set(f'{len(self.found)} dispositivo(s)/configuração(ões) encontrado(s). Selecione uma linha e explore.' if self.found else 'Nenhuma resposta válida. Confira porta, alimentação e A/B; tente outra função/endereço de descoberta.')
            elif kind=='row':
                self.rows.append(data)
                if not self.nonzero.get() or data['value']!=0:self.registers.insert('','end',values=self.row_values(data))
            elif kind=='read_progress':
                done,total,fc,address,summary=data;self.progress.configure(value=100*done/total)
                self.progress_text.set(f'{done}/{total} endereços • FC{fc:02d} • offset {address} • {summary["valid"]} leituras válidas')
            elif kind=='read_done':
                skipped=', '.join(f'FC{i:02d}' for i in data['unsupported']) or 'nenhuma'
                self.status.set(f'Concluído: {data["valid"]} leituras válidas; {data["invalid"]} endereços inválidos; {data["timeouts"]} sem resposta/CRC; {data["exceptions"]} outras exceções. Funções não suportadas: {skipped}.')
            elif kind=='float_done':
                fc,value=data
                self.float_result.set(f'{value:.9g}  |  FC{fc:02d}, 0x0016–0x0017, ABCD')
                self.status.set('Medição Float32 lida.')
            elif kind=='apply_done':
                self.float_result.set('')
                self.endpoint=data;port,baud,parity,sid=data;self.found=[(baud,parity,sid)];self.rows=[];self.render_rows()
                self.devices.delete(*self.devices.get_children());self.devices.insert('','end',iid='0',values=(sid,baud,f'8{parity}1 — verificado por leitura'))
                self.selection_text.set(f'{port}  •  Slave ID {sid}  •  {baud} baud  •  8{parity}1')
                self.status.set('Gravação confirmada pelo Anhui. Para conferir a persistência, desligue, religue e faça uma nova busca.')
            elif kind=='cancelled':
                if data=='scan':self.invalidate()
                self.status.set('Busca interrompida. Nenhuma escrita enviada. As leituras já obtidas podem ser exportadas.' if data=='explore' else 'Descoberta interrompida. Faça uma nova busca para selecionar um dispositivo.')
            elif kind=='port_error':
                if data in self.bluetooth_ports:
                    adapters = ', '.join(self.usb_ports)
                    guidance = (f'Selecione a porta USB do conversor RS485 ({adapters}) e tente novamente.'
                                if adapters else 'Conecte o conversor USB/RS485 e clique em Atualizar portas.')
                    self.status.set(f'Não foi possível abrir {data} (Bluetooth). {guidance}')
                else:
                    self.status.set(f'Não foi possível abrir {data}. Confira se o conversor está conectado, '
                                    'feche outros programas que usam a porta e tente novamente.')
            elif kind=='error':
                mode,error=data
                if mode=='apply':self.invalidate()
                self.status.set(f'Falha: {error}. '+('Pode haver configuração parcial; veja o log e detecte novamente.' if mode=='apply' else 'Confira a porta, os cabos e o dispositivo.'))
            elif kind=='idle':
                self.busy=None
                if self.found and self.endpoint is None:
                    self.devices.selection_set('0');self.select_device()
                self.count_text.set(f'{len(self.registers.get_children())} exibido(s) / {len(self.rows)} leitura(s) válida(s). CSV inclui zeros.')
                self.update_controls()
                if self.closing:self.destroy();return
        self.poll_id=self.root.after(80,self.poll)

    def on_close(self):
        if self.busy=='apply':self.status.set('Aguarde a configuração e a verificação antes de fechar.')
        elif self.busy:self.closing=True;self.stop()
        else:self.destroy()

    def destroy(self):
        try:self.root.after_cancel(self.poll_id)
        except tk.TclError:pass
        for handler in self.handlers:protocol.LOG.removeHandler(handler);handler.close()
        self.root.destroy()

def main():
    root=tk.Tk();App(root);root.mainloop()

if __name__=='__main__':main()
