$WshShell = New-Object -ComObject WScript.Shell
$DesktopPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
$ShortcutPath = Join-Path $DesktopPath "Invertir PDF (Modo Oscuro).lnk"
$TargetBat = Join-Path $PSScriptRoot "ARRASTRA_AQUI_TU_PDF.bat"

$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $TargetBat
$Shortcut.WorkingDirectory = $PSScriptRoot
$Shortcut.Description = "Arrastra archivos PDF aqui para convertirlos a Modo Oscuro"
$Shortcut.Save()

Write-Host "Acceso directo creado en el Escritorio: $ShortcutPath"
