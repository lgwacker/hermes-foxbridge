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

## Configuração (variáveis de ambiente)

| Var | Padrão | Função |
|---|---|---|
| `FOXBRIDGE_CDP_URL` | `http://127.0.0.1:9222` | Endpoint CDP que o provider entrega ao Hermes |
| `FOXBRIDGE_CONTAINER` | `foxbridge` | Nome do container Docker |
| `FOXBRIDGE_IMAGE` | `ghcr.io/lgwacker/foxbridge-camoufox:latest` | Imagem do sidecar |
| `FOXBRIDGE_IDLE_TIMEOUT_S` | `900` | Segundos de idle antes de parar o sidecar |

## Desenvolvimento

```bash
python -m pytest tests/ -q          # testes unitários (sem Docker, sem Hermes)
./scripts/build-image.sh            # build local da imagem do sidecar
```

CI: testes unitários a cada push; build+push da imagem ao GHCR no `main`.

## Roadmap

- [ ] Upstream foxbridge: flag `--host` (elimina a necessidade de `--network host`) e binários de release (elimina o passo de build golang)
- [ ] Contextos de browser por sessão (`Target.createBrowserContext`) para isolamento de cookies
- [ ] Tags de imagem por versão do Camoufox

## Licença

MIT
