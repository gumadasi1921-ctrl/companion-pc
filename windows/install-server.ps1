# Companion PC - instala o servidor no Windows e faz ele iniciar com o logon.
# Uso (não precisa de Administrador):
#   powershell -ExecutionPolicy Bypass -File install-server.ps1
param([string]$Source = $PSScriptRoot)

$Dest = Join-Path $env:USERPROFILE "companion-pc\server"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
# Migra token, deck e histórico da instalação antiga (r36s-companion), se existir
$OldDir = Join-Path $env:USERPROFILE "r36s-companion\server"
foreach ($f in "config.json", "deck.json", "games-history.json") {
    $src = Join-Path $OldDir $f
    if ((Test-Path $src) -and -not (Test-Path (Join-Path $Dest $f))) { Copy-Item $src $Dest; Write-Host "Migrado: $f" }
}
Copy-Item (Join-Path $Source "server\*.py") $Dest -Force
# App web (servido pelo próprio servidor na porta 8770)
New-Item -ItemType Directory -Force -Path (Join-Path $Dest "web") | Out-Null
Copy-Item (Join-Path $Source "server\web\*") (Join-Path $Dest "web") -Recurse -Force

Write-Host "Instalando dependências..."
py -3 -m pip install --user --disable-pip-version-check -q --no-warn-script-location `
    websockets psutil pillow pycaw comtypes pystray qrcode `
    winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation `
    winrt-Windows.Foundation.Collections winrt-Windows.Storage.Streams `
    winrt-Windows.UI.Notifications winrt-Windows.UI.Notifications.Management winrt-Windows.ApplicationModel

# pythonw = sem janela de console
$pyw = (py -3 -c "import sys, pathlib; print(pathlib.Path(sys.executable).with_name('pythonw.exe'))").Trim()
$script = Join-Path $Dest "companion_server.py"

# Ícone dos atalhos: companion.ico gerado do ícone do app (16/32/48/256 px), se ainda não existir
$ico = Join-Path $Dest "companion.ico"
if (-not (Test-Path $ico)) {
    py -3 -c "import sys; from PIL import Image; im = Image.open(sys.argv[1]).convert('RGBA').resize((256, 256), Image.LANCZOS); im.save(sys.argv[2], sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])" (Join-Path $Dest "web\icon-192.png") $ico
}

$sh = New-Object -ComObject WScript.Shell
function New-Shortcut($lnk) {
    $s = $sh.CreateShortcut($lnk)
    $s.TargetPath = $pyw
    $s.Arguments = "`"$script`""
    $s.WorkingDirectory = $Dest
    $s.Description = "Companion PC - servidor (ícone na bandeja)"
    if (Test-Path $ico) { $s.IconLocation = "$ico,0" }
    $s.Save()
    Write-Host "Atalho: $lnk"
}
# Remove atalhos da versão antiga
foreach ($dir in [Environment]::GetFolderPath("Startup"), [Environment]::GetFolderPath("Programs")) {
    Remove-Item -LiteralPath (Join-Path $dir "R36S PC Companion.lnk") -ErrorAction SilentlyContinue
}
# Inicia junto com o Windows (sem janela, só o ícone na bandeja)
New-Shortcut (Join-Path ([Environment]::GetFolderPath("Startup")) "Companion PC.lnk")
# Menu Iniciar: abrir pelo menu mostra o painel (ou inicia o servidor se estiver parado)
New-Shortcut (Join-Path ([Environment]::GetFolderPath("Programs")) "Companion PC.lnk")

# Reinicia o servidor
$old = Get-CimInstance Win32_Process |
    Where-Object { $_.ProcessId -ne $PID -and $_.Name -match '^(py|pythonw?)\.exe$' -and $_.CommandLine -match 'companion_server\.py' }
$old | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 1
Start-Process -FilePath $pyw -ArgumentList "`"$script`"" -WorkingDirectory $Dest
Start-Sleep 3
Get-Content (Join-Path $Dest "companion-server.log") -Tail 3
Write-Host "Token: veja 'token' em $Dest\config.json"
Write-Host "Abra o painel (ícone na bandeja) e escaneie o QR code. Uma vez: windows\setup-network.ps1"
