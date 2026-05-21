# register-monitor-task.ps1
# Registra "FlowsurfaceMonitor" en el Programador de tareas de Windows.
# Corre como el usuario actual (sin contraseña visible) al iniciar sesión.
# Ejecutar con: powershell -ExecutionPolicy Bypass -File register-monitor-task.ps1

$TaskName  = "FlowsurfaceMonitor"
$ScriptDir = $PSScriptRoot
$BatPath   = Join-Path $ScriptDir "start-monitor.bat"

if (-not (Test-Path $BatPath)) {
    Write-Error "No se encontró start-monitor.bat en $ScriptDir"
    exit 1
}

# Remove old task if exists
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tarea anterior eliminada."
}

# Action: run the bat in a minimized cmd window
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$BatPath`"" `
    -WorkingDirectory $ScriptDir

# Trigger: at logon of the current user
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Settings: allow running when on AC or battery, restart on failure
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

# Principal: run as current user, only when logged in (no UAC elevation)
$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $Action `
    -Trigger   $Trigger `
    -Settings  $Settings `
    -Principal $Principal `
    -Description "Flowsurface headless strategy monitor (MongoDB local)" `
    -Force

Write-Host ""
Write-Host "Tarea '$TaskName' registrada correctamente."
Write-Host "Se ejecutará automáticamente al iniciar sesión con: $env:USERNAME"
Write-Host ""
Write-Host "Para iniciarla ahora: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Para detenerla:       Stop-ScheduledTask  -TaskName '$TaskName'"
Write-Host "Para ver estado:      Get-ScheduledTask   -TaskName '$TaskName' | Select-Object TaskName,State"
