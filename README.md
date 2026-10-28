# Innotech — Scanner de Slave Perdido

Scanner Modbus RTU para Windows, com descoberta de Slave ID/baudrate, exploração de tabelas e um perfil documentado para configuração do transdutor de pressão/nível.

## Executar no Windows

Abra **[Innotech_Scanner_de_Slave_Perdido.exe](Innotech_Scanner_de_Slave_Perdido.exe)** com dois cliques. Não precisa instalar Python.

1. Selecione a porta do conversor USB/RS485.
2. Clique em **Encontrar dispositivos** e selecione o resultado.
3. Na aba **Explorar tabelas**, escolha as funções e a faixa de endereços.
4. Consulte os valores brutos ou exporte para CSV.

A descoberta e a exploração são somente leitura. A alteração de ID e baudrate exige selecionar o perfil do transdutor correspondente ao manual incluído; não existe mapa universal de configuração Modbus.

Leia [as instruções completas](LEIA-ME_Innotech_Scanner.txt) para limites da busca, endereçamento, configuração serial e interpretação dos resultados.

## Conteúdo

| Arquivo/pasta | Finalidade |
|---|---|
| `Innotech_Scanner_de_Slave_Perdido.exe` | Interface principal com identidade Innotech |
| `Innotech_Scanner_de_Slave_Perdido_Pacote.zip` | Distribuição completa do scanner |
| `Fontes_Innotech_Scanner/` | Código Python, recursos gráficos e testes |
| `Manual_Anhui_Modbus.pdf` | Manual que fundamenta o perfil específico de escrita |
| `configure_anhui_transducer.py` | Script de descoberta/configuração para ID 2 e 9600 baud |
| `Anhui_Configurador*`, `Anhui_ID2_9600*`, `Anhui_Fontes_GUI/` | Versões anteriores e respectivos fontes/pacotes |
| `ESP32S3_I2C_SDA8_SCL9*` | Firmware e projeto auxiliar de scanner I²C, SDA GPIO8 / SCL GPIO9 |
| `SHA256*.txt` | Verificação de integridade dos artefatos distribuídos |
| `logs/` | Histórico de execução presente na pasta de entrega |

O firmware I²C incluído nesta pasta foi empacotado para **100 kHz**. A versão posterior de 10 kHz utilizada na placa foi preparada fora desta pasta e não substitui os artefatos aqui arquivados.

## Executar pelos fontes

Requer Python com Tkinter e `pyserial`:

```powershell
python -m pip install pyserial
cd Fontes_Innotech_Scanner
python innotech_scanner.py
```

Testes offline da distribuição principal:

```powershell
python -m unittest discover -p "test*.py"
```

As instruções de recompilação e os detalhes de validação estão nos arquivos `LEIA-ME*.txt`. Os executáveis não têm assinatura digital. As representações UInt16/Int16/hexadecimal não substituem o mapa, as unidades ou a escala definidos pelo fabricante.

## Origem

Importação da pasta de entrega `outputs` do projeto local, incluindo os artefatos e versões anteriores, sem alterar o comportamento do programa.
