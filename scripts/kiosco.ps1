# Arranca el mostrador a pantalla completa y lo mantiene abierto.
# Salida de técnico: Ctrl+Mayús+F12 devuelve el escritorio de Windows.
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$puerto = if ($env:FACTURACION_PUERTO) { [int]$env:FACTURACION_PUERTO } else { 8099 }
$url = "http://127.0.0.1:$puerto/"
$marca = "FacturacionKiosco"
$perfil = Join-Path $env:LOCALAPPDATA $marca
$log = Join-Path $raiz "kiosco.log"

function Anotar([string]$texto) {
    $linea = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $texto
    Add-Content -Path $log -Value $linea -ErrorAction SilentlyContinue
}

$mutex = New-Object System.Threading.Mutex($false, "Local\$marca")
if (-not $mutex.WaitOne(0)) { exit 0 }

Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class KioscoTeclas {
    public static volatile bool Salir = false;
    private const int WH_KEYBOARD_LL = 13;
    private const int WM_KEYDOWN = 0x0100;
    private const int WM_SYSKEYDOWN = 0x0104;
    private static IntPtr _hook = IntPtr.Zero;
    private static readonly LowLevelProc _proc = Hook;
    private delegate IntPtr LowLevelProc(int nCode, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] private static extern IntPtr SetWindowsHookEx(int id, LowLevelProc proc, IntPtr hMod, uint thread);
    [DllImport("user32.dll")] private static extern IntPtr CallNextHookEx(IntPtr h, int nCode, IntPtr wParam, IntPtr lParam);
    [DllImport("kernel32.dll")] private static extern IntPtr GetModuleHandle(string name);
    [DllImport("user32.dll")] private static extern short GetAsyncKeyState(int vKey);
    [DllImport("user32.dll")] private static extern uint MsgWaitForMultipleObjects(uint nCount, IntPtr pHandles, bool bWaitAll, uint dwMilliseconds, uint dwWakeMask);
    public static void Esperar(uint ms) {
        MsgWaitForMultipleObjects(0, IntPtr.Zero, false, ms, 0x04FF);
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct KBDLLHOOKSTRUCT {
        public uint vkCode;
        public uint scanCode;
        public uint flags;
        public uint time;
        public IntPtr dwExtraInfo;
    }
    public static void Instalar() {
        _hook = SetWindowsHookEx(WH_KEYBOARD_LL, _proc, GetModuleHandle(null), 0);
    }
    private static bool Pulsada(int vk) { return (GetAsyncKeyState(vk) & 0x8000) != 0; }
    private static IntPtr Hook(int nCode, IntPtr wParam, IntPtr lParam) {
        if (nCode >= 0 && (wParam == (IntPtr)WM_KEYDOWN || wParam == (IntPtr)WM_SYSKEYDOWN)) {
            KBDLLHOOKSTRUCT info = (KBDLLHOOKSTRUCT)Marshal.PtrToStructure(lParam, typeof(KBDLLHOOKSTRUCT));
            uint vk = info.vkCode;
            bool alt = (info.flags & 0x20) != 0 || Pulsada(0x12);
            bool ctrl = Pulsada(0x11);
            bool shift = Pulsada(0x10);
            if (ctrl && shift && vk == 0x7B) { Salir = true; return (IntPtr)1; }
            if (vk == 0x5B || vk == 0x5C) return (IntPtr)1;
            if (vk == 0x7A) return (IntPtr)1;
            if (alt && (vk == 0x09 || vk == 0x73 || vk == 0x1B || vk == 0x20)) return (IntPtr)1;
            if (ctrl && shift && vk == 0x1B) return (IntPtr)1;
            if (ctrl && (vk == 0x57 || vk == 0x4E || vk == 0x54 || vk == 0x4C)) return (IntPtr)1;
        }
        return CallNextHookEx(_hook, nCode, wParam, lParam);
    }
}
"@

function Puerto-Abierto {
    try {
        $cliente = New-Object System.Net.Sockets.TcpClient
        $cliente.Connect("127.0.0.1", $puerto)
        $cliente.Close()
        return $true
    } catch {
        return $false
    }
}

function Resolver-Python {
    $venv = Join-Path $raiz ".venv\Scripts\python.exe"
    if (Test-Path $venv) { return $venv }
    $comando = Get-Command python -ErrorAction SilentlyContinue
    if ($comando) { return $comando.Source }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    throw "No encontré Python en esta computadora."
}

function Iniciar-Servidor {
    if (Puerto-Abierto) { return }
    $python = Resolver-Python
    Anotar "Abriendo el programa con $python"
    if ((Split-Path -Leaf $python) -eq "py.exe") {
        Start-Process -FilePath $python -ArgumentList @("-3", "web_server.py") -WorkingDirectory $raiz -WindowStyle Hidden
    } else {
        Start-Process -FilePath $python -ArgumentList @("web_server.py") -WorkingDirectory $raiz -WindowStyle Hidden
    }
}

function Esperar-Programa {
    $limite = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $limite) {
        if (Puerto-Abierto) { return }
        [KioscoTeclas]::Esperar(200)
        [System.Windows.Forms.Application]::DoEvents()
    }
    throw "El programa no respondió en el puerto $puerto."
}

function Resolver-Navegador {
    $candidatos = @(
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "${env:ProgramFiles}\Microsoft\Edge\Application\msedge.exe"
    )
    foreach ($ruta in $candidatos) {
        if ($ruta -and (Test-Path $ruta)) { return $ruta }
    }
    throw "No está Chrome ni Edge."
}

function Argumentos-Navegador([string]$exe) {
    $args = @(
        "--kiosk", $url,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--disable-pinch",
        "--overscroll-history-navigation=0",
        "--user-data-dir=$perfil"
    )
    if ((Split-Path -Leaf $exe) -like "msedge.exe") {
        $args = @("--edge-kiosk-type=fullscreen") + $args
    }
    return $args
}

function Procesos-Kiosco {
    if (-not $script:navegador) { return @() }
    $nombre = if ((Split-Path -Leaf $script:navegador) -like "msedge.exe") { "msedge.exe" } else { "chrome.exe" }
    Get-CimInstance Win32_Process -Filter "Name = '$nombre'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$marca*" }
}

function Abrir-Pantalla {
    $lista = Argumentos-Navegador $script:navegador
    Anotar "Abriendo pantalla $($script:navegador)"
    Start-Process -FilePath $script:navegador -ArgumentList $lista | Out-Null
}

try {
    Set-Location $raiz
    [KioscoTeclas]::Instalar()
    $script:navegador = Resolver-Navegador
    Iniciar-Servidor
    Esperar-Programa
    Abrir-Pantalla
    $ultimo = Get-Date
    $ultimoServidor = Get-Date
    while (-not [KioscoTeclas]::Salir) {
        [System.Windows.Forms.Application]::DoEvents()
        if (-not (Puerto-Abierto) -and ((Get-Date) - $ultimoServidor).TotalSeconds -gt 15) {
            Anotar "El programa se detuvo. Lo vuelvo a abrir."
            Iniciar-Servidor
            $ultimoServidor = Get-Date
        }
        $vivos = @(Procesos-Kiosco)
        if ($vivos.Count -eq 0 -and ((Get-Date) - $ultimo).TotalSeconds -gt 4) {
            Anotar "La pantalla se cerró. La vuelvo a abrir."
            Abrir-Pantalla
            $ultimo = Get-Date
        }
        [KioscoTeclas]::Esperar(200)
        [System.Windows.Forms.Application]::DoEvents()
    }
    Anotar "Salida de técnico."
} catch {
    Anotar $_.Exception.Message
    [System.Windows.Forms.MessageBox]::Show(
        $_.Exception.Message,
        "Facturación",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
} finally {
    foreach ($proceso in @(Procesos-Kiosco)) {
        Stop-Process -Id $proceso.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) {
        Start-Process explorer.exe
    }
    $mutex.ReleaseMutex() | Out-Null
}
