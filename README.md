# Hub da organização ceia-pdc-slms

Site em **https://ceia-pdc-slms.github.io** que junta, numa página só, todos os HTML que os times publicam nos repositórios da org: laboratórios, aulas, documentos, leaderboards e posts.

Ninguém precisa mexer neste repo para aparecer no hub: **commite um `.html` em qualquer repo público da org** e ele entra na próxima build (a cada hora, ou na hora se o repo avisar; veja abaixo).

## Como funciona

`build.py` (só biblioteca padrão, Python 3.13+):

1. lista os repos da org. Repos privados ficam de fora, a não ser que tenham `publish = true` no `hub.toml`;
2. clona cada um e encontra todos os `.html`;
3. **repo com GitHub Pages próprio** (ex.: `pdc-inferencia`): o hub só aponta para o site dele, em `/<repo>/`;
   **repo sem Pages** (ex.: `fase-1-nivelamento`): o hub copia o repo para `/<repo>/` e publica junto. Os links relativos entre as páginas continuam funcionando, e se o repo não tiver `index.html` o hub gera um;
4. lê título, descrição, data e autores (do git) de cada página e monta `index.html` e `catalogo.json`.

```
hub.toml            configuração: textos, regras de tipo, ajustes por repo e por arquivo
build.py            o build
templates/          página inicial e página de repo
assets/             tokens.css, tema.css e tema.js vêm do pdc-inferencia; hub.css é do hub
posts/              posts escritos direto no hub (um .html por post, ou posts/<slug>/index.html)
.github/workflows/  publica no GitHub Pages: a cada push, a cada hora e sob demanda
```

## Ajustar como uma página aparece

O jeito preferido é usar metatags na própria página:

```html
<title>Como um modelo gera texto</title>
<meta name="description" content="Prefill, decode e KV cache.">
<meta name="hub:kind" content="laboratorio">        <!-- post, leaderboard, laboratorio, ferramenta, aula, documento -->
<meta name="hub:authors" content="Beatriz, Marcos">  <!-- sem isso, usa quem commitou o arquivo -->
<meta name="hub:hidden" content="true">              <!-- não lista no hub -->
```

Quando não der para mexer na página, use `[files]` ou `[repos.<nome>]` no `hub.toml`. Sem metatag, o tipo sai do caminho do arquivo (`laboratorios/`, `apresentacoes/`, `docs/`…), conforme as regras em `kinds`.

## Rodar localmente

```sh
python3 build.py                 # usa `gh auth token` para clonar
python3 -m http.server -d _site  # http://localhost:8000
python3 build.py --offline       # rebuild rápido, sem buscar nada (só templates/CSS)
```

## Atualizar o hub assim que um repo-fonte mudar (opcional)

Sem isso, o hub pega as mudanças em até uma hora. Para ser imediato, crie um segredo de organização `HUB_DISPATCH_TOKEN` (token fine-grained com *Actions: write* neste repo) e adicione ao repo-fonte o arquivo `.github/workflows/avisar-hub.yml`:

```yaml
name: Avisar o hub
on:
  push:
    branches: [main]
    paths: ["**.html"]
jobs:
  avisar:
    runs-on: ubuntu-latest
    steps:
      - run: gh api repos/ceia-pdc-slms/ceia-pdc-slms.github.io/dispatches -f event_type=fonte-atualizada
        env:
          GH_TOKEN: ${{ secrets.HUB_DISPATCH_TOKEN }}
```

## Publicar pela primeira vez

1. Crie o repo **`ceia-pdc-slms/ceia-pdc-slms.github.io`** (público; precisa ter exatamente esse nome) e faça push deste diretório.
2. Em *Settings → Pages → Build and deployment*, escolha **GitHub Actions**.
3. Rode o workflow *Publicar hub* (ou faça um push).
