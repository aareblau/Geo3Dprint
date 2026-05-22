param(
    [ValidateSet("right", "max")]
    [string]$Mode = "right"
)

$ErrorActionPreference = "SilentlyContinue"

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public struct RECT {
    public int Left;
    public int Top;
    public int Right;
    public int Bottom;
}

public static class Geo3DprintWin32 {
    [DllImport("kernel32.dll")]
    public static extern IntPtr GetConsoleWindow();

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool SetWindowPos(
        IntPtr hWnd,
        IntPtr hWndInsertAfter,
        int X,
        int Y,
        int cx,
        int cy,
        uint uFlags
    );

    [DllImport("user32.dll")]
    public static extern bool SystemParametersInfo(
        int uiAction,
        int uiParam,
        ref RECT pvParam,
        int fWinIni
    );
}
"@

function Get-UsableWindowHandle {
    $console = [Geo3DprintWin32]::GetConsoleWindow()
    if ($console -ne [IntPtr]::Zero -and [Geo3DprintWin32]::IsWindowVisible($console)) {
        $rect = New-Object RECT
        [void][Geo3DprintWin32]::GetWindowRect($console, [ref]$rect)
        if (($rect.Right - $rect.Left) -gt 100 -and ($rect.Bottom - $rect.Top) -gt 100) {
            return $console
        }
    }

    $foreground = [Geo3DprintWin32]::GetForegroundWindow()
    if ($foreground -ne [IntPtr]::Zero) {
        return $foreground
    }

    return [IntPtr]::Zero
}

$hwnd = Get-UsableWindowHandle
if ($hwnd -eq [IntPtr]::Zero) {
    exit 0
}

if ($Mode -eq "max") {
    [void][Geo3DprintWin32]::ShowWindow($hwnd, 3)
    exit 0
}

$workArea = New-Object RECT
if (-not [Geo3DprintWin32]::SystemParametersInfo(0x0030, 0, [ref]$workArea, 0)) {
    exit 0
}

$left = $workArea.Left
$top = $workArea.Top
$width = $workArea.Right - $workArea.Left
$height = $workArea.Bottom - $workArea.Top
$halfWidth = [Math]::Max(400, [Math]::Floor($width / 2))

[void][Geo3DprintWin32]::ShowWindow($hwnd, 9)
[void][Geo3DprintWin32]::SetWindowPos(
    $hwnd,
    [IntPtr]::Zero,
    $left + $halfWidth,
    $top,
    $width - $halfWidth,
    $height,
    0x0040
)
