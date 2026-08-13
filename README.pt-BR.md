# Hermes Foxbridge

Provedor de browser do Hermes que dirige um **browser anti-detect Camoufox** (fork do Firefox com spoofing de fingerprint em C++) através do proxy **foxbridge** CDP→Juggler — dando ao `browser_exec` (harness do browser-use CLI) e às ferramentas de browser built-in um backend stealth, sem Chrome e sem nuvem.

```
Hermes (browser_exec ou browser_navigate/click/...)
   │ CDP
   ▼
foxbridge (sidecar Docker, porta 9222)
   │ Juggler pipe
   ▼
Camoufox (fork anti-detect do Firefox)
```

## Por quê

| | Chrome + harness | Camoufox + foxbridge (este repo) |
|---|---|---|
| Stealth anti-bot | trivialmente detectável | spoofing de fingerprint em C++ (canvas, WebGL, áudio, fontes, ...) |
| Qualidade do harness | browser-use CLI (SOTA em benchmarks web) | mesmo harness, mesma qualidade |
| Custo | grátis (local) ou créditos cloud | grátis, local, sessões ilimitadas |
| Persistência | — | por perfil via Camoufox |

## Componentes

- `plugins/browser/foxbridge/` — plugin do Hermes (`kind: backend`, `provides_browser_providers: [foxbridge]`). Selecione com `browser.cloud_provider: foxbridge`.
- `docker/Dockerfile` — imagem `foxbridge-camoufox`: foxbridge + bundle Camoufox (construída sobre a imagem camofox-browser), publicada em `ghcr.io/lgwacker/foxbridge-camoufox` pelo CI.
- `docker/bootstrap.mjs` — bootstrap dinâmico: chama o mesmo `camoufox-js launchOptions()` que o servidor camofox usa, escreve `CAMOU_CONFIG_1..N` / `FONTCONFIG_PATH` / `DISPLAY` no env do sidecar, `firefoxUserPrefs` no perfil e embute o addon uBO no config de fingerprint. Seeds aleatórios por boot = sem identidade fixa.
- `patches/` — os **três patches obrigatórios** do foxbridge (veja [`patches/README.md`](patches/README.md)): `foxbridge-fetch-noop.patch` (fix do deadlock do Juggler), `foxbridge-mainframe-context.patch` (fix do drift para iframes de anúncio) e `foxbridge-host-flag.patch` (rede bridge: o sidecar usa mapeamentos `-p` em vez de `--network host`). Os três estão embutidos no binário commitado e na imagem publicada.
- O ciclo de vida do provider espelha a integração camofox: o sidecar **sobe no primeiro uso** e **para após `FOXBRIDGE_IDLE_TIMEOUT_S`** (padrão 900 s) sem atividade.

## Instalação

```bash
# 1. Plugin (espaço do usuário; nada instalado no sistema)
hermes plugins install lgwacker/hermes-foxbridge/plugins/browser/foxbridge --enable

# 2. Imagem do sidecar (só no primeiro uso — o provider cria/inicia o
#    container sob demanda; a imagem é puxada automaticamente)
docker pull ghcr.io/lgwacker/foxbridge-camoufox:latest

# 3. Selecionar o provider
hermes config set browser.cloud_provider foxbridge
```

Requisitos: Docker e, para a superfície `browser_exec`, o CLI browser-use
(`hermes tools` → Browser Automation → Browser Use instala; as ferramentas
built-in funcionam sem ele).

> ⚠️ `CAMOFOX_URL` NÃO pode estar setado em `~/.hermes/.env` — o backend
> camofox tem precedência sobre qualquer `browser.cloud_provider` (design do
> Hermes). Remova-o para este provider atender o tráfego de browser.

> ⚠️ Inicie uma **nova sessão** após instalar (a seleção do provider é lida
> uma vez por processo e fica em cache).

## Configuração (variáveis de ambiente)

| Var | Padrão | Função |
|---|---|---|
| `FOXBRIDGE_CDP_URL` | `http://127.0.0.1:9222` | Endpoint CDP que o provider entrega ao Hermes |
| `FOXBRIDGE_CDP_PORT` | `9222` | Porta CDP que o sidecar escuta (mova para longe do Chrome de cron do Hermes) |
| `FOXBRIDGE_CONTAINER` | `foxbridge` | Nome do container Docker |
| `FOXBRIDGE_IMAGE` | `ghcr.io/lgwacker/foxbridge-camoufox:latest` | Imagem do sidecar |
| `FOXBRIDGE_IDLE_TIMEOUT_S` | `900` | Segundos de idle antes de parar o sidecar |
| `FOXBRIDGE_VNC` | `0` | `1` liga o VNC/noVNC do sidecar para logins interativos |
| `FOXBRIDGE_VNC_PORT` | `5901` | Porta do x11vnc (loopback do host) |
| `FOXBRIDGE_VNC_NOVNC_PORT` | `6081` | Porta web do noVNC (loopback do host) |
| `FOXBRIDGE_VNC_BIND` | `127.0.0.1` | Endereço de bind do noVNC |
| `FOXBRIDGE_VNC_PASSWORD` | — | Senha opcional do x11vnc |
| `FOXBRIDGE_VNC_VIEW_ONLY` | `0` | `1` = VNC somente leitura |

## Logins interativos via VNC (opcional)

Alguns sites exigem login manual (2FA, CAPTCHA, SSO incomum). A imagem do
sidecar já traz a stack VNC do camofox (x11vnc + noVNC) — o provider a liga
com `FOXBRIDGE_VNC=1`:

1. Defina `FOXBRIDGE_VNC=1` (ex.: em `~/.hermes/.env`) e inicie uma sessão
   de browser. O sidecar passa a escutar também em `127.0.0.1:5901`
   (x11vnc) e `127.0.0.1:6081` (noVNC).
2. Abra <http://127.0.0.1:6081/vnc.html> e faça o login manualmente — é a
   mesma instância do Camoufox que o CDP dirige.
3. O login persiste no volume de perfil (`~/.hermes/foxbridge-profiles/`),
   então sessões automatizadas seguintes já começam logadas.

Observações:

- O VNC escuta só no loopback do host e morre junto com o sidecar:
  idle-stop após `FOXBRIDGE_IDLE_TIMEOUT_S` ou o restart que todo
  `create_session` faz. Aumente `FOXBRIDGE_IDLE_TIMEOUT_S` enquanto faz
  login interativo e defina `FOXBRIDGE_VNC_PASSWORD` se for expor além do
  loopback.
- As portas 5901/6081 evitam as 5900/6080 do servidor camofox-browser
  (o sidecar compartilha o namespace de rede do host).

## Armadilhas conhecidas

- **Race do `wait_for_load()` com `about:blank`** — o harness considera a
  página carregada assim que `document.readyState` é `complete`, o que já é
  verdade no `about:blank`. Logo após `new_tab(url)`, o `wait_for_load()`
  pode retornar antes da navegação commitar e o `page_info()` ainda mostra
  `about:blank`. Workaround: faça polling do `page_info()` algumas vezes com
  pequenos sleeps, ou use `ensure_real_tab()` + `goto_url(url)` em vez de
  `new_tab()` (verificado carregando limpo através desta stack).
- `CAMOFOX_URL` não pode estar setado (veja Instalação).
- **Ressurreição do sessionstore** — o Camoufox restaura abas antigas
  (Google Sign-In, páginas de anúncio) do sessionstore do perfil no restart
  do sidecar; o harness pode anexar num iframe de anúncio em vez da página
  principal. Apague `recovery*.lz4` / `sessionstore*` em
  `~/.hermes/foxbridge-profiles/` antes de reiniciar o sidecar manualmente.
- **Conflito de porta 9222 com o Chrome de cron do Hermes** — o Chrome em
  modo cron do gateway (`chrome-debug-cron`) ocupa `127.0.0.1:9222`
  enquanto o daemon está de pé, então o foxbridge do sidecar não consegue
  dar bind e as sessões falham (`address already in use` no
  `docker logs foxbridge`). Defina `FOXBRIDGE_CDP_PORT` (ex.: `9223`) — o
  provider repassa ao container e deriva o endpoint CDP a partir dela.
- **Daemon do browser-use stale** — após restart manual do sidecar, mate o
  daemon do harness (`pkill -f "browser_harness[.]daemon"`); o provider faz
  isso automaticamente no `create_session`.

## Desenvolvimento

```bash
python -m pytest tests/ -q          # testes unitários (sem Docker, sem Hermes)
./scripts/build-image.sh            # build local da imagem do sidecar
```

`build-image.sh` reconstrói o binário foxbridge a partir do upstream `7dee166`
e aplica **ambos** os patches em ordem (noop primeiro, mainframe segundo) —
veja [`patches/README.md`](patches/README.md) para saber por que a ordem
importa e como atualizar a ref do upstream.

CI: testes unitários a cada push; build+push da imagem ao GHCR no `main`
(usa o binário **commitado** `docker/foxbridge` — deliberadamente NÃO
`go install @latest`, que descartaria os patches silenciosamente).

## Roadmap

- [x] Flag `--host` — feita localmente via `patches/foxbridge-host-flag.patch`: o sidecar agora usa rede bridge com `-p` só-loopback (sem mais `--network host`)
- [ ] Upstream foxbridge: binários de release (elimina o passo de build golang)
- [ ] Contextos de browser por sessão (`Target.createBrowserContext`) para isolamento de cookies
- [ ] Tags de imagem por versão do Camoufox

## Licença

MIT
