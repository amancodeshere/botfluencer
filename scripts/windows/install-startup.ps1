# Run once in Administrator PowerShell as the Windows user who owns Ubuntu.
# Does not store a Windows password or enable automatic Windows sign-in.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ListenAddress,
    [Parameter(Mandatory = $true)][string]$AllowedClient,
    [string]$Distro = 'Ubuntu',
    [ValidateRange(1024, 65535)][int]$Port = 2222,
    [switch]$Monitor
)

$ErrorActionPreference = 'Stop'
foreach ($address in @($ListenAddress, $AllowedClient)) {
    $parsed = [System.Net.IPAddress]::Parse($address)
    if ($parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
        throw 'This installer requires IPv4 addresses.'
    }
    $bytes = $parsed.GetAddressBytes()
    if ($bytes[0] -ne 100 -or $bytes[1] -lt 64 -or $bytes[1] -gt 127) {
        throw 'Use Tailscale addresses in 100.64.0.0/10; refusing a public/LAN listener.'
    }
}
if ($Distro -notmatch '^[a-zA-Z0-9._-]+$') { throw 'Invalid distribution name.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]$identity
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open Administrator PowerShell as the Windows user who owns Ubuntu.'
}
$installDir = Join-Path $env:ProgramData 'BotfluencerRemoteAccess'
$installedScript = Join-Path $installDir 'install-startup.ps1'
$logPath = Join-Path $installDir 'startup.log'
$taskName = 'Botfluencer-WSL-SSH'

if ($Monitor) {
    $keepAlive = $null
    $lastAddress = $null
    while ($true) {
        try {
            Start-Service iphlpsvc
            if (-not (Get-NetIPAddress -IPAddress $ListenAddress -ErrorAction SilentlyContinue)) {
                throw 'Tailscale interface is not ready.'
            }
            & wsl.exe -d $Distro -u root -- systemctl start ssh
            if ($LASTEXITCODE -ne 0) { throw 'Ubuntu SSH startup failed.' }
            if ($null -eq $keepAlive -or $keepAlive.HasExited) {
                $keepAlive = Start-Process wsl.exe -ArgumentList @(
                    '-d', $Distro, '-u', 'root', '--', '/usr/bin/sleep', 'infinity'
                ) -WindowStyle Hidden -PassThru
            }
            $addressOutput = & wsl.exe -d $Distro hostname -I
            if ($LASTEXITCODE -ne 0) { throw 'Could not read Ubuntu address.' }
            $targetAddress = (($addressOutput -join ' ').Trim() -split '\s+')[0]
            [void][System.Net.IPAddress]::Parse($targetAddress)
            if ($targetAddress -ne $lastAddress) {
                & netsh.exe interface portproxy set v4tov4 "listenaddress=$ListenAddress" `
                    "listenport=$Port" "connectaddress=$targetAddress" connectport=22 | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    & netsh.exe interface portproxy add v4tov4 "listenaddress=$ListenAddress" `
                        "listenport=$Port" "connectaddress=$targetAddress" connectport=22 | Out-Null
                    if ($LASTEXITCODE -ne 0) { throw 'Port forwarding update failed.' }
                }
                $lastAddress = $targetAddress
                Add-Content $logPath ((Get-Date).ToUniversalTime().ToString('o') + ' event=forwarding_ready')
            }
        } catch {
            $lastAddress = $null
            Add-Content $logPath ((Get-Date).ToUniversalTime().ToString('o') +
                ' event=startup_retry error_type=' + $_.Exception.GetType().Name)
        }
        if ((Test-Path $logPath) -and (Get-Item $logPath).Length -gt 1MB) {
            Set-Content $logPath ((Get-Date).ToUniversalTime().ToString('o') + ' event=log_rotated')
        }
        Start-Sleep -Seconds 30
    }
}

# WSL registrations belong to a Windows user; never run this task as SYSTEM.
$distributions = (& wsl.exe --list --quiet) -replace "`0", ''
if ($LASTEXITCODE -ne 0 -or ($distributions.Trim() -notcontains $Distro)) {
    throw 'This Windows account does not own the requested WSL distribution.'
}
$tailscale = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
if (-not (Test-Path $tailscale)) { throw 'Install and sign in to Windows Tailscale first.' }

New-Item -ItemType Directory -Path $installDir -Force | Out-Null
& icacls.exe $installDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' `
    '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not protect startup script directory.' }
& icacls.exe $installDir /setowner '*S-1-5-32-544' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not set startup directory owner.' }
Copy-Item -LiteralPath $PSCommandPath -Destination $installedScript -Force
& icacls.exe $installedScript /setowner '*S-1-5-32-544' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not protect startup script ownership.' }
& $tailscale set --unattended=true
if ($LASTEXITCODE -ne 0) { throw 'Could not enable unattended Tailscale.' }

$firewall = @{
    Name = $taskName
    Direction = 'Inbound'
    Action = 'Allow'
    Protocol = 'TCP'
    LocalAddress = $ListenAddress
    LocalPort = $Port
    RemoteAddress = $AllowedClient
    Profile = 'Any'
}
if (Get-NetFirewallRule -Name $taskName -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule @firewall | Out-Null
} else {
    New-NetFirewallRule @firewall -DisplayName 'Botfluencer SSH over Tailscale' | Out-Null
}
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "' +
    $installedScript + '" -Monitor -ListenAddress ' + $ListenAddress +
    ' -AllowedClient ' + $AllowedClient + ' -Distro ' + $Distro + ' -Port ' + $Port
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn -User $identity.Name)
)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType S4U -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers `
    -Principal $taskPrincipal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output 'Startup task installed. Verify startup.log, SSH access, and a planned reboot.'
