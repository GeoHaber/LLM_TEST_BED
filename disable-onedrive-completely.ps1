param(
    [switch]$PurgeLocalOneDriveData,
    [switch]$SkipFolderMove,
    [switch]$NoRebootPrompt
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "`n[+] $Message" -ForegroundColor Cyan
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[!] $Message" -ForegroundColor Yellow
}

function Ensure-Directory {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Move-OneDriveContentSafely {
    param(
        [string]$Source,
        [string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        return
    }

    Ensure-Directory -Path $Destination

    $items = Get-ChildItem -LiteralPath $Source -Force -ErrorAction SilentlyContinue
    foreach ($item in $items) {
        $target = Join-Path $Destination $item.Name

        if (Test-Path -LiteralPath $target) {
            $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
            $renamedTarget = "{0}_from_onedrive_{1}" -f $item.BaseName, $stamp
            if ($item.PSIsContainer) {
                $target = Join-Path $Destination $renamedTarget
            }
            else {
                $target = Join-Path $Destination ("{0}{1}" -f $renamedTarget, $item.Extension)
            }
        }

        Move-Item -LiteralPath $item.FullName -Destination $target -Force
    }
}

function Set-DwordPolicy {
    param(
        [string]$Path,
        [string]$Name,
        [int]$Value
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -Path $Path -Force | Out-Null
    }

    New-ItemProperty -Path $Path -Name $Name -PropertyType DWord -Value $Value -Force | Out-Null
}

function Is-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Is-Admin)) {
    throw "Run this script in an elevated PowerShell window (Run as Administrator)."
}

Write-Step "Stopping OneDrive processes"
Get-Process OneDrive -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Write-Step "Disabling OneDrive auto-start for current user"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (Test-Path -LiteralPath $runKey) {
    Remove-ItemProperty -Path $runKey -Name "OneDrive" -ErrorAction SilentlyContinue
}

Write-Step "Disabling OneDrive scheduled tasks"
$taskPatterns = @(
    "OneDrive Reporting Task-*",
    "OneDrive Standalone Update Task-*"
)

foreach ($pattern in $taskPatterns) {
    $tasks = Get-ScheduledTask -TaskName $pattern -ErrorAction SilentlyContinue
    foreach ($task in $tasks) {
        Disable-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction SilentlyContinue | Out-Null
        Unregister-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -Confirm:$false -ErrorAction SilentlyContinue
    }
}

Write-Step "Uninstalling OneDrive"
$installerCandidates = @(
    "$env:SystemRoot\SysWOW64\OneDriveSetup.exe",
    "$env:SystemRoot\System32\OneDriveSetup.exe"
)

$uninstalled = $false
foreach ($installer in $installerCandidates) {
    if (Test-Path -LiteralPath $installer) {
        Start-Process -FilePath $installer -ArgumentList "/uninstall" -Wait -NoNewWindow
        $uninstalled = $true
        break
    }
}

if (-not $uninstalled) {
    Write-Warn "OneDriveSetup.exe not found. Continuing with policy lock-down."
}

Write-Step "Blocking OneDrive via policy"
Set-DwordPolicy -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\OneDrive" -Name "DisableFileSyncNGSC" -Value 1
Set-DwordPolicy -Path "HKCU:\SOFTWARE\Policies\Microsoft\OneDrive" -Name "DisableFileSyncNGSC" -Value 1

Write-Step "Removing OneDrive from File Explorer sidebar"
$oneDriveClsids = @(
    "HKCR:\CLSID\{018D5C66-4533-4307-9B53-224DE2ED1FE6}",
    "HKCR:\Wow6432Node\CLSID\{018D5C66-4533-4307-9B53-224DE2ED1FE6}"
)

foreach ($clsidPath in $oneDriveClsids) {
    if (Test-Path -LiteralPath $clsidPath) {
        New-ItemProperty -Path $clsidPath -Name "System.IsPinnedToNameSpaceTree" -PropertyType DWord -Value 0 -Force | Out-Null
    }
}

$userProfile = $env:USERPROFILE
$localDesktop = Join-Path $userProfile "Desktop"
$localDocuments = Join-Path $userProfile "Documents"
$localPictures = Join-Path $userProfile "Pictures"

$oneDriveRoot = Join-Path $userProfile "OneDrive"
$oneDriveDesktop = Join-Path $oneDriveRoot "Desktop"
$oneDriveDocuments = Join-Path $oneDriveRoot "Documents"
$oneDrivePictures = Join-Path $oneDriveRoot "Pictures"

Write-Step "Restoring Desktop/Documents/Pictures to local profile paths"
$userShellFolders = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
if (-not (Test-Path -LiteralPath $userShellFolders)) {
    New-Item -Path $userShellFolders -Force | Out-Null
}

Set-ItemProperty -Path $userShellFolders -Name "Desktop" -Value "%USERPROFILE%\Desktop" -Type ExpandString
Set-ItemProperty -Path $userShellFolders -Name "Personal" -Value "%USERPROFILE%\Documents" -Type ExpandString
Set-ItemProperty -Path $userShellFolders -Name "My Pictures" -Value "%USERPROFILE%\Pictures" -Type ExpandString

Ensure-Directory -Path $localDesktop
Ensure-Directory -Path $localDocuments
Ensure-Directory -Path $localPictures

if (-not $SkipFolderMove) {
    Write-Step "Moving files from OneDrive folders to local folders"
    Move-OneDriveContentSafely -Source $oneDriveDesktop -Destination $localDesktop
    Move-OneDriveContentSafely -Source $oneDriveDocuments -Destination $localDocuments
    Move-OneDriveContentSafely -Source $oneDrivePictures -Destination $localPictures
}
else {
    Write-Warn "SkipFolderMove is set. Files were not moved from OneDrive folders."
}

if ($PurgeLocalOneDriveData) {
    Write-Step "Purging local OneDrive data"
    $purgeTargets = @(
        $oneDriveRoot,
        "$env:LOCALAPPDATA\Microsoft\OneDrive",
        "$env:PROGRAMDATA\Microsoft OneDrive"
    )

    foreach ($target in $purgeTargets) {
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Step "Restarting Explorer shell"
Get-Process explorer -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Process explorer.exe

Write-Host "`nCompleted. OneDrive is disabled and blocked on this PC." -ForegroundColor Green
Write-Host "Cloud data deletion is separate: remove files at onedrive.live.com and empty the OneDrive recycle bin." -ForegroundColor Yellow

if (-not $NoRebootPrompt) {
    $answer = Read-Host "Reboot now to finalize all policy and shell changes? (Y/N)"
    if ($answer -match '^(Y|y)$') {
        Restart-Computer -Force
    }
}
