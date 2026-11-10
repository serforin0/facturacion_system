# Deja el mostrador para que abra solo al entrar a Windows.
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$kiosco = Join-Path $raiz "scripts\kiosco.ps1"
$inicio = [Environment]::GetFolderPath("Startup")
$acceso = Join-Path $inicio "Facturacion.lnk"

$shell = New-Object -ComObject WScript.Shell
$atajo = $shell.CreateShortcut($acceso)
$atajo.TargetPath = "powershell.exe"
$atajo.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$kiosco`""
$atajo.WorkingDirectory = $raiz
$atajo.WindowStyle = 7
$atajo.Description = "Mostrador de facturación"
$atajo.Save()
Write-Host "Listo. Al entrar a Windows se abre el mostrador y no se puede cerrar."
Write-Host "Para volver al escritorio, el técnico pulsa Ctrl+Mayús+F12."

$respuesta = Read-Host "¿Windows debe entrar solo al encender la computadora? (s/n)"
if ($respuesta -eq "s" -or $respuesta -eq "S") {
    $usuario = Read-Host "Usuario de Windows"
    if (-not $usuario) { $usuario = $env:USERNAME }
    $segura = Read-Host "Contraseña de Windows" -AsSecureString
    $puntero = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($segura)
    try {
        $clave = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($puntero)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($puntero)
    }
    try {
        $ruta = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
        Set-ItemProperty -Path $ruta -Name AutoAdminLogon -Value "1"
        Set-ItemProperty -Path $ruta -Name DefaultUserName -Value $usuario
        Set-ItemProperty -Path $ruta -Name DefaultPassword -Value $clave
        Set-ItemProperty -Path $ruta -Name DefaultDomainName -Value $env:COMPUTERNAME
        Write-Host "Windows entrará solo con $usuario."
    } catch {
        Write-Host "No pude dejar el arranque automático. Clic derecho en instalar_kiosco.bat y elige Ejecutar como administrador."
    }
    $clave = $null
}

$ahora = Read-Host "¿Abrir el mostrador ahora? (s/n)"
if ($ahora -eq "s" -or $ahora -eq "S") {
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", $kiosco
    ) -WorkingDirectory $raiz
}
