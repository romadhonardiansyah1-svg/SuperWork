param([string]$DesktopDirectory = "")
$ErrorActionPreference = "Stop"
$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$launcherPath = Join-Path $workspaceRoot "Start-SuperWork.vbs"
if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
    throw "Start-SuperWork.vbs tidak ditemukan."
}
$shell = New-Object -ComObject WScript.Shell
if (-not $DesktopDirectory) { $DesktopDirectory = $shell.SpecialFolders.Item("Desktop") }
if (-not (Test-Path -LiteralPath $DesktopDirectory -PathType Container)) {
    throw "Folder Desktop tidak ditemukan."
}
$shortcutPath = Join-Path $DesktopDirectory "SuperWork.lnk"
$targetPath = Join-Path $env:SystemRoot "System32\wscript.exe"
$arguments = '"' + $launcherPath + '"'
$shortcut = $shell.CreateShortcut($shortcutPath)
if ((Test-Path -LiteralPath $shortcutPath) -and
    (($shortcut.TargetPath -ine $targetPath) -or ($shortcut.Arguments -cne $arguments))) {
    throw "SuperWork.lnk sudah ada dan menuju aplikasi lain. Shortcut tersebut tidak diubah."
}
$shortcut.TargetPath = $targetPath
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $workspaceRoot
$shortcut.Description = "Buka dashboard lokal SuperWork dan kelola 9router tanpa terminal."
$shortcut.IconLocation = (Join-Path $env:SystemRoot "System32\shell32.dll") + ",44"
$shortcut.WindowStyle = 7
$shortcut.Save()
Write-Output $shortcutPath
