# Companion PC - libera o acesso pela rede local (e, opcional, pelo cabo USB do Android).
# Rode UMA vez (duplo clique > Executar com o PowerShell, ou no terminal):
#   powershell -ExecutionPolicy Bypass -File setup-network.ps1
#  1) libera a porta 8770 no Firewall (só redes Privadas) -> Wi-Fi   [pede admin (UAC)]
#  2) instala o adb (Android Platform Tools) -> cabo USB              [sem admin]
# O winget NÃO pode rodar como Administrador (ele recusa), por isso só o
# firewall é elevado.
param([int]$Port = 8770)

$name = "Companion PC ($Port)"
if (Get-NetFirewallRule -DisplayName $name, "R36S PC Companion - celular ($Port)" -ErrorAction SilentlyContinue) {
    Write-Host "Firewall: regra da porta $Port já existe."
} else {
    Write-Host "Firewall: liberando a porta $Port (o Windows vai pedir permissão)..."
    $fw = "New-NetFirewallRule -DisplayName '$name' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Private | Out-Null"
    try {
        Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command", $fw
        Write-Host "Firewall: porta $Port liberada em redes Privadas."
    } catch { Write-Warning "Permissão negada: o Wi-Fi não vai funcionar até liberar a porta $Port." }
}
$pub = Get-NetConnectionProfile | Where-Object NetworkCategory -eq Public
if ($pub) {
    Write-Warning "A rede '$($pub.Name -join ', ')' ($($pub.InterfaceAlias -join ', ')) está como Pública: o celular não conecta pelo Wi-Fi."
    Write-Warning "Se for a rede da sua casa, mude para Privada em Configurações > Rede e Internet > (sua rede) > Tipo de perfil de rede."
}

function Install-Pkg($id, $label) {
    $found = winget list --id $id -e --accept-source-agreements 2>$null | Select-String $id
    if ($found) { Write-Host "$label já instalado."; return }
    Write-Host "Instalando $label..."
    winget install --id $id -e --accept-source-agreements --accept-package-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { Write-Warning "Não consegui instalar $label pelo winget." }
}
Install-Pkg "Google.PlatformTools" "adb (Android Platform Tools)"
Write-Host ""
Write-Host "Para usar pelo cabo: no celular ative Opções do desenvolvedor > Depuração USB,"
Write-Host "conecte no PC e aceite 'Permitir depuração USB'. Depois abra http://localhost:$Port no Chrome."
Write-Host "(O servidor procura o adb sozinho a cada 30 s; não precisa reiniciar.)"
