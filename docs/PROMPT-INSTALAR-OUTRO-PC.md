# Prompt — instalar o Companion PC (versão nova) em outro computador

Cole o bloco abaixo no **Claude Code** aberto **no computador onde vai instalar**
(Claude Code nativo do Windows, não WSL nem sessão isolada). Antes, troque
`<SEU-USUARIO>` pelo seu usuário do GitHub.

---

```text
Tarefa: instalar a versão mais nova do Companion PC neste computador Windows, a partir do meu repositório privado https://github.com/<SEU-USUARIO>/companion-pc. Sou leigo: explique cada passo que EU precisar fazer em linguagem simples.

## 0. Ambiente (confirme antes de tudo)
- Confirme que você está rodando direto no Windows (PowerShell/cmd nativo), NÃO em WSL, VM ou sandbox. Se não estiver, PARE e me passe os comandos para eu rodar no meu PowerShell (o instalador precisa rodar no Windows de verdade, senão os pacotes do Python ficam invisíveis e o atalho não abre).
- Pasta de destino: %USERPROFILE%\Documents\Companion-PC
  - Se não existir: clonar o repositório nela.
  - Se já existir e for um clone deste repo: `git status` precisa estar limpo; se não estiver, PARE e me relate (não descarte nada). Se estiver limpo: `git pull --ff-only`.
  - Se existir mas não for um clone (ex.: pacote .zip antigo extraído): NÃO apague; renomeie para Companion-PC-antigo e clone ao lado.
- Ao final do passo 0, informe o commit (HEAD) e a data dele.

## Objetivo (só isto)
1. Garantir pré-requisitos: Git, GitHub CLI e Python 3.12 (instalar com winget --scope user só o que faltar; conferir com `git --version`, `gh --version`, `py -3 --version`).
2. Autenticar no GitHub para clonar o repo privado: rode `gh auth login` e deixe EU fazer o login no navegador. Nunca peça nem digite senha ou token no chat.
3. Clonar/atualizar o repositório (passo 0).
4. Rodar o instalador: `powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\Documents\Companion-PC\windows\install-server.ps1"` (sem administrador). Ele copia para %USERPROFILE%\companion-pc\server, instala os pacotes, cria os atalhos e abre o servidor.
5. Rede: antes do setup-network, me guie para deixar a rede atual como **Privada** (Configurações > Rede e Internet > rede > Tipo de perfil de rede > Privada) e espere minha confirmação. Depois rode `windows\setup-network.ps1` (vai pedir administrador uma vez — eu clico em Sim). Ele cria a regra da porta 8770 só no perfil Privado e instala o adb.
6. Conferir que funcionou (ver "Verificação").
NÃO expanda o escopo: não mexa em código, não "melhore" scripts, não faça commit nem push neste PC.

## Regras críticas
- Firewall: NUNCA crie nem deixe regra liberada no perfil **Público** (nem para o pythonw.exe). Se o Windows perguntar sobre o Python no firewall, me avise para marcar SÓ "Redes privadas". Depois do setup, liste as regras com `Get-NetFirewallRule | Where-Object DisplayName -match 'python|Companion|8770'` e mostre o perfil de cada uma; se alguma estiver em Public/Any, me relate e pergunte antes de alterar.
- config.json tem o token e o panel_token: nunca imprima o conteúdo dele no chat nem em log. Não copie config.json de outro PC — este PC gera um token novo.
- Não apague nada em %USERPROFILE%\companion-pc\server (deck.json, tasks.json, work, pomodoro.json são dados meus). Se o instalador falhar, PARE e relate o erro; não tente "consertar" editando arquivos do projeto.
- Se faltar permissão, rede ou algo que só eu posso fazer (login, clicar em Sim, mudar a rede), PARE e me diga exatamente o que fazer.

## Opcional (só se eu disser "sim" quando você perguntar)
- Usar os mesmos botões do outro PC: sair do Companion pela bandeja, copiar extras\deck.json para %USERPROFILE%\companion-pc\server\ (substituindo) e abrir de novo pelo atalho "Companion PC".
- Pasta de clientes (tempo de trabalho): me lembre de abrir o painel > Trabalho > "Pastas de clientes" > Adicionar e escolher a pasta dos clientes deste PC.

## Verificação
- O processo do servidor está rodando e a porta 8770 está ouvindo (`Get-NetTCPConnection -LocalPort 8770 -State Listen`).
- `Invoke-WebRequest http://127.0.0.1:8770 -UseBasicParsing` responde 200 (não imprima o corpo).
- Atalhos "Companion PC" existem no Menu Iniciar, na Área de Trabalho e na inicialização do Windows.
- Se houver %USERPROFILE%\companion-pc\server\companion-server-erro.log com erro recente, mostre só as últimas linhas (sem tokens).
- Testes rápidos do projeto: `py -3 tests/test_pair.py` e `py -3 tests/test_tasks_restore.py` (rodam numa cópia em outra porta; se algum falhar, só relate).
- Me lembre do celular: mesma rede Wi-Fi, abrir o painel > Status > escanear o QR code (o token é novo; o app já instalado vai pedir de novo — escanear resolve).

## Relatório final (no máximo ~10 linhas)
- Commit instalado (HEAD + data).
- Pré-requisitos: o que já tinha e o que foi instalado.
- Instalador: ok / erro.
- Rede: perfil da rede atual + perfis das regras de firewall (confirmar que nenhuma está em Público).
- Verificação e testes: ok / o que falhou.
- Pendências que dependem de mim.
Termine com "PRONTO PARA USAR" (ou "BLOQUEADO: <motivo>") e pare.
```

---

## Para atualizar depois (mesmo PC)

Basta pedir ao Claude Code: *"Atualize o Companion PC: `git pull --ff-only` em
Documents\Companion-PC e rode windows\install-server.ps1 de novo. Não mexa na
rede nem no firewall. Relate o commit novo."* — rodar o instalador de novo é a
forma de atualizar.
