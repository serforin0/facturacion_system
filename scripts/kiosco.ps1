# Abre SistemaFacturacion.exe a pantalla completa y lo vuelve a abrir si se cierra.
# El técnico vuelve al escritorio con Ctrl+Mayús+F12.
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$marca = "FacturacionEscritorio"
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
            if (ctrl && shift && vk == 0x7B) return CallNextHookEx(_hook, nCode, wParam, lParam);
            if (vk == 0x5B || vk == 0x5C || vk == 0x7A) return (IntPtr)1;
            if (alt && (vk == 0x09 || vk == 0x73 || vk == 0x1B || vk == 0x20)) return (IntPtr)1;
            if (ctrl && shift && vk == 0x1B) return (IntPtr)1;
        }
        return CallNextHookEx(_hook, nCode, wParam, lParam);
    }
}
"@

function Resolver-Exe {
    $candidatos = @(
        (Join-Path $raiz "SistemaFacturacion.exe"),
        (Join-Path $raiz "dist\SistemaFacturacion.exe")
    )
    foreach ($ruta in $candidatos) {
        if (Test-Path $ruta) { return $ruta }
    }
    return $null
}

function Programa-Abierto {
    return [bool](Get-Process -Name "SistemaFacturacion" -ErrorAction SilentlyContinue)
}

$salida = $null
try {
    Set-Location $raiz
    $exe = Resolver-Exe
    if (-not $exe) { throw "No está SistemaFacturacion.exe en esta carpeta." }
    $carpeta = Split-Path $exe
    $salida = Join-Path $carpeta "kiosco.salir"
    if (Test-Path $salida) { Remove-Item $salida -Force }
    New-Item -ItemType File -Path (Join-Path (Split-Path $exe) "kiosco.flag") -Force | Out-Null
    [KioscoTeclas]::Instalar()
    Anotar "Abriendo $exe"
    Start-Process -FilePath $exe -ArgumentList @("--kiosco") -WorkingDirectory $carpeta | Out-Null
    $ultimo = Get-Date
    while (-not (Test-Path $salida)) {
        [System.Windows.Forms.Application]::DoEvents()
        if (-not (Programa-Abierto) -and ((Get-Date) - $ultimo).TotalSeconds -gt 4) {
            if (Test-Path $salida) { break }
            Anotar "El programa se cerró. Lo vuelvo a abrir."
            Start-Process -FilePath $exe -ArgumentList @("--kiosco") -WorkingDirectory $carpeta | Out-Null
            $ultimo = Get-Date
        }
        [KioscoTeclas]::Esperar(200)
        [System.Windows.Forms.Application]::DoEvents()
    }
    Anotar "Salida de técnico."
    Get-Process -Name "SistemaFacturacion" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
} catch {
    Anotar $_.Exception.Message
    [System.Windows.Forms.MessageBox]::Show(
        $_.Exception.Message,
        "Facturación",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
} finally {
    if ($salida -and (Test-Path $salida)) { Remove-Item $salida -Force -ErrorAction SilentlyContinue }
    if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) {
        Start-Process explorer.exe
    }
    $mutex.ReleaseMutex() | Out-Null
}
