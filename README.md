# THE JARVIS SYSTEM

Arquitetura local de IAs no Windows 11 + RTX 4070 Super, operada por uma **CLI no PowerShell**
(rich + prompt_toolkit), com voz, terminal web de apoio para o celular, automações técnicas e
parada de emergência no F1.

| Agente | Função | Onde |
|---|---|---|
| 🤖 **JARVIS** | orquestrador, voz, interface | `modules/core.py`, modelo `jarvis` |
| 💼 **MONDAY** | WhatsApp, agenda, rotina | `modules/tool_whatsapp.py` |
| 💻 **ROMEU** | Python, APIs, scripts | persona no modelo `jarvis` |
| ⚙️ **PAPA** | Fusion 360, 3D, Open Interpreter `--os` | `modules/tool_fusion.py` |
| 📊 **PLATO** | matemática, BOM no Google Sheets | `modules/tool_sheets.py` |
| 🛡️ **FRIDAY** | QA/QC de toda saída técnica | `modules/tool_reviewer.py`, modelo `friday` |

## Estrutura

```
jarvis_system/
├── jarvis_cli.py            # interface principal (banner, painéis, spinner, prompt, F1)
├── jarvis_master_system.py  # servidor web de apoio (FastAPI/WebSockets, porta 8000)
├── modules/
│   ├── core.py              # orquestrador compartilhado: roteamento, agentes, pipeline FRIDAY
│   ├── llm.py               # cliente Ollama (streaming cancelável)
│   ├── telemetry.py         # CPU/RAM/GPU/VRAM
│   ├── voice_engine.py      # openWakeWord + Faster-Whisper + Piper
│   ├── tool_sheets.py · tool_fusion.py · tool_whatsapp.py · tool_reviewer.py
│   └── kill_switch.py       # F1 global: parada (IDLE) ou encerramento total
├── templates/index.html     # HUD web para o celular
├── Modelfile_Jarvis · Modelfile_Friday
└── setup.ps1 · start_jarvis.ps1 · tests/
```

## Instalação

> Use um caminho curto (ex.: `C:\jarvis_system`). Caminhos longos estouram o limite de 260
> caracteres do Windows ao criar o venv.

```powershell
cd C:\jarvis_system
powershell -ExecutionPolicy Bypass -File .\setup.ps1             # tudo
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -SkipModels # modelos já baixados
```

O `setup.ps1` cria dois ambientes:

- **`.venv`** (Python do sistema): CLI, servidor, voz.
- **`.venv-oi`** (Python 3.11 via `uv`): Open Interpreter. Ele não instala em 3.13 (`tiktoken`
  antigo) e precisa de `setuptools<81`. O caminho vai para `OI_PYTHON` no `.env`.

## Uso

```powershell
.\start_jarvis.ps1            # CLI
.\start_jarvis.ps1 -Web       # CLI + terminal web no celular (link com token no banner)
.\start_jarvis.ps1 -Voice     # CLI + “Hey Jarvis”
python jarvis_cli.py --once "/status" --once "Romeu, ..."   # não interativo
```

No prompt `JARVIS:\>`:

- ↑/↓ percorrem o histórico.
- **Tab** completa os comandos `/`.
- A barra inferior mostra CPU, RAM, GPU, VRAM e temperatura ao vivo.
- O spinner indica o agente e a etapa em curso, por exemplo `[PAPA] Gerando script do Fusion 360…` e
  depois `[FRIDAY] Auditando script do Fusion 360…`.

As respostas aparecem em painéis com o selo `🛡️ QA Status: PASSED/CORRECTED/FLAGGED`. Os blocos de
código são exibidos com destaque de sintaxe.

| Comando | Ação |
|---|---|
| texto livre | JARVIS roteia por palavra-chave ou chamada direta (“Romeu, …”) |
| `/agents` · `/status` · `/help` | agentes · telemetria · comandos |
| `/agent <nome> <texto>` | força um agente |
| `/bom Projeto \| descrição` | PLATO → JSON → FRIDAY → tabela + aba no Google Sheets |
| `/zap contato \| pedido` → `/send` | MONDAY redige → FRIDAY revisa → só envia após `/send` |
| `/zapbom contato` · `/whatsapp +55... texto` | relatório da BOM · envio literal |
| `/fusion placa c=100 l=60 e=5 furo=5 margem=8` | script parametrizado na pasta Scripts do Fusion |
| `/fusion <descrição>` | PAPA escreve o script → validação → FRIDAY → salva |
| `/os <instrução>` | Open Interpreter `--os` com visão local em console próprio |
| `/voice on\|off` · `/clear` · `/cls` · `/exit` | voz · memória · limpar tela · sair |
| **F1** · `/kill` · Ctrl+C (durante geração) | ⛔ PARADA DE EMERGÊNCIA → IDLE |
| `/shutdown` | encerra JARVIS, Ollama e ferramentas |

## Parada de emergência

| Modo | O que acontece |
|---|---|
| **CLI** (F1, `/kill`, `kill` no celular) | Cancela a geração em curso. Fecha o stream e o Ollama para de gerar na GPU. Encerra Open Interpreter, drivers Selenium e subprocessos. Apaga o spinner e volta ao prompt em IDLE. JARVIS e Ollama continuam vivos. |
| **`/shutdown`**, ou servidor standalone (`python jarvis_master_system.py`) | Encerramento total: o modo acima, mais o Ollama, e `os._exit`. |

Limitações:

- O pywhatkit usa o seu navegador padrão. A parada não fecha o navegador pessoal.
- Se um programa estiver rodando como Administrador e com foco, o hook não vê o F1 dele.
  Nesse caso, rode o JARVIS também como Administrador.

## Voz

A wake word pré-treinada é **“Hey Jarvis”**. Para usar “Ok Jarvis”, treine um modelo customizado do
openWakeWord e configure `WAKEWORD_MODEL=...onnx`.

```powershell
.\.venv\Scripts\python -m modules.voice_engine --say "Sistemas online, senhor."
```

## Testes

```powershell
.\.venv\Scripts\python tests\test_kill_switch.py      # F1 global (dry-run)
.\.venv\Scripts\python tests\smoke_server.py          # com o servidor web rodando
```
