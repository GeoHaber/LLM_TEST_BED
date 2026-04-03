# Install llama-cpp-python on Windows. Run with venv activated: .\install_llama_cpp_windows.ps1
# Default: wheels only. Use -BuildFromSource to build with Vulkan + native CPU (AVX2/AVX512) support (needs Visual Studio).
# Use -UpdateBinary to also download the latest llama-server.exe (Vulkan build) to C:\AI\_bin.
# Use -UpdatePython  to check PyPI for a newer llama-cpp-python wheel and upgrade if available.
# Use -CheckUpdate   to print version status without installing anything.

param([switch]$BuildFromSource, [switch]$UpdateBinary, [switch]$UpdatePython, [switch]$CheckUpdate)
$ErrorActionPreference = "Stop"

# ----- Version check helpers -----
function Get-InstalledLlamaCppVersion {
    try {
        $out = & pip show llama-cpp-python 2>$null | Select-String "^Version:"
        if ($out) { return ($out -split ":\s*")[1].Trim() }
    } catch { }
    return $null
}

function Get-PyPILatestVersion {
    param([string]$Package)
    try {
        $resp = Invoke-RestMethod -Uri "https://pypi.org/pypi/$Package/json" `
            -Headers @{"Accept"="application/json"; "User-Agent"="ZenAIos"} -TimeoutSec 10
        return $resp.info.version
    } catch {
        Write-Host "PyPI lookup failed: $_" -ForegroundColor Yellow
        return $null
    }
}

function Compare-Versions {
    param([string]$Installed, [string]$Latest)
    try {
        $a = [Version]($Installed -replace "[^0-9.]","")
        $b = [Version]($Latest    -replace "[^0-9.]","")
        return $b -gt $a
    } catch { return $false }
}

# ----- Check-only mode -----
if ($CheckUpdate) {
    Write-Host "Checking llama-cpp-python versions..." -ForegroundColor Cyan
    $installed = Get-InstalledLlamaCppVersion
    $latest    = Get-PyPILatestVersion "llama-cpp-python"
    if ($installed) {
        Write-Host "  Installed : $installed"
    } else {
        Write-Host "  Installed : (not found)"
    }
    if ($latest) {
        Write-Host "  PyPI latest: $latest"
        if ($installed -and (Compare-Versions $installed $latest)) {
            Write-Host "  --> Update available! Run with -UpdatePython to upgrade." -ForegroundColor Yellow
        } elseif ($installed) {
            Write-Host "  --> Up to date." -ForegroundColor Green
        }
    }
    # Also check llama-server binary if present
    $binExe = "C:\AI\_bin\llama-server.exe"
    if (Test-Path $binExe) {
        $ver = (& $binExe --version 2>&1 | Select-String "version:") -replace ".*version:\s*", "" -replace "\s.*",""
        Write-Host "  llama-server.exe: build $ver  (run -UpdateBinary to refresh)"
    }
    exit 0
}

# ----- Upgrade Python wheel if newer on PyPI -----
if ($UpdatePython) {
    Write-Host "Checking llama-cpp-python on PyPI..." -ForegroundColor Cyan
    $installed = Get-InstalledLlamaCppVersion
    $latest    = Get-PyPILatestVersion "llama-cpp-python"
    if (-not $latest) {
        Write-Host "Could not reach PyPI. Aborting -UpdatePython." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Installed : $(if ($installed) { $installed } else { '(none)' })"
    Write-Host "  Latest    : $latest"
    if ($installed -and -not (Compare-Versions $installed $latest)) {
        Write-Host "  Already up to date ($installed). Nothing to do." -ForegroundColor Green
        if (-not $UpdateBinary -and -not $BuildFromSource) { exit 0 }
    } else {
        Write-Host "  Upgrading llama-cpp-python $installed -> $latest ..." -ForegroundColor Yellow
        # Use --upgrade (not exact pinning) so pip picks the best available wheel
        # for this Python version — exact pins fail when a wheel isn't published yet.
        $upgraded = $false
        pip install --upgrade llama-cpp-python --only-binary :all: --quiet 2>$null
        if ($LASTEXITCODE -eq 0) { $upgraded = $true }
        if (-not $upgraded) {
            pip install --upgrade llama-cpp-python --only-binary :all: `
                --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/cpu" --quiet 2>$null
            if ($LASTEXITCODE -eq 0) { $upgraded = $true }
        }
        if (-not $upgraded) {
            Write-Host "  No prebuilt wheel for $latest on Python $($PSVersionTable.PSVersion). " -ForegroundColor Yellow
            Write-Host "  Try: .\install_llama_cpp_windows.ps1 -BuildFromSource" -ForegroundColor Gray
            exit 1
        }
        $now = Get-InstalledLlamaCppVersion
        Write-Host "  Done. Installed: $now" -ForegroundColor Green
    }
    if (-not $UpdateBinary -and -not $BuildFromSource) { exit 0 }
}

# ----- Download latest llama-server binary -----
if ($UpdateBinary) {
    Write-Host "Checking latest llama.cpp release..." -ForegroundColor Cyan
    $apiUrl = "https://api.github.com/repos/ggerganov/llama.cpp/releases/latest"
    try {
        $release = Invoke-RestMethod -Uri $apiUrl -Headers @{"User-Agent"="ZenAIos"} -TimeoutSec 10
        $tag = $release.tag_name
        Write-Host "Latest release: $tag ($($release.published_at.Substring(0,10)))"

        # Pick Vulkan build for AMD/cross-platform GPU support
        $asset = $release.assets | Where-Object { $_.name -like "*-win-vulkan-x64.zip" } | Select-Object -First 1
        if (-not $asset) {
            Write-Host "No Vulkan Windows x64 build found in release $tag" -ForegroundColor Red
            exit 1
        }

        $binDir = "C:\AI\_bin"
        if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }

        # Backup current install if llama-server.exe exists
        $currentExe = Join-Path $binDir "llama-server.exe"
        if (Test-Path $currentExe) {
            $currentVer = (& $currentExe --version 2>&1 | Select-String "version:" | ForEach-Object { ($_ -split "version:\s*")[1].Split(" ")[0] })
            if ($currentVer) {
                $backupDir = "C:\AI\_bin_backup_b$currentVer"
                if (-not (Test-Path $backupDir)) {
                    Write-Host "Backing up current build $currentVer..."
                    Copy-Item -Path $binDir -Destination $backupDir -Recurse
                }
            }
        }

        $zipPath = Join-Path $env:TEMP $asset.name
        $extractDir = Join-Path $env:TEMP "llama-extract-temp"

        Write-Host "Downloading $($asset.name) ($([math]::Round($asset.size/1MB)) MB)..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath -UseBasicParsing

        if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force }
        Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

        # Handle possible subfolder inside zip
        $srcDir = $extractDir
        $subItems = Get-ChildItem $extractDir
        if ($subItems.Count -eq 1 -and $subItems[0].PSIsContainer) { $srcDir = $subItems[0].FullName }

        Get-ChildItem $srcDir -File | ForEach-Object { Copy-Item $_.FullName -Destination (Join-Path $binDir $_.Name) -Force }

        # Cleanup
        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
        Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue

        # Verify
        $newVer = & $currentExe --version 2>&1 | Select-String "version:"
        Write-Host "Updated: $newVer" -ForegroundColor Green
    } catch {
        Write-Host "Binary update failed: $_" -ForegroundColor Red
        exit 1
    }
    if (-not $BuildFromSource) { exit 0 }
}

# ----- Build from source (AVX2 off) -----
if ($BuildFromSource) {
    try { pip uninstall llama-cpp-python llama-cpp-python-win -y 2>&1 | Out-Null } catch { }

    # Find nmake: 1) already in PATH, 2) vswhere, 3) one explicit path
    $nmake = Get-Command nmake -ErrorAction SilentlyContinue
    $vcvars64 = $null
    if (-not $nmake) {
        $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
        if (Test-Path $vswhere) {
            $vsPath = & $vswhere -latest -products * -property installationPath 2>$null
            if ($vsPath) {
                $vcvars64 = Join-Path $vsPath "VC\Auxiliary\Build\vcvars64.bat"
                if (-not (Test-Path $vcvars64)) { $vcvars64 = $null }
            }
        }
        if (-not $vcvars64) {
            $candidate = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
            if (Test-Path $candidate) { $vcvars64 = $candidate }
        }
    }

    if (-not $nmake -and -not $vcvars64) {
        Write-Host "Visual Studio Build Tools required. Install: https://aka.ms/vs/17/release/vs_BuildTools.exe (Desktop development with C++)" -ForegroundColor Red
        Write-Host "Or run without -BuildFromSource to use prebuilt wheel." -ForegroundColor Gray
        exit 1
    }

    Write-Host "Installing build deps..." -ForegroundColor Yellow
    pip install cmake "scikit-build-core[pyproject]" setuptools wheel flit_core numpy --only-binary numpy --quiet
    if ($LASTEXITCODE -ne 0) { Write-Host "pip install failed." -ForegroundColor Red; exit 1 }

    $env:FORCE_CMAKE = "1"
    # Zen 5 (Ryzen AI HX 370): enable Vulkan GPU offload + native AVX2/AVX512 VNNI.
    # Radeon iGPU uses Vulkan; GGML_NATIVE lets the compiler pick the best ISA for this CPU.
    $env:CMAKE_ARGS = "-DGGML_VULKAN=ON -DGGML_NATIVE=ON -DGGML_AVX2=ON -DGGML_AVX512=ON"
    if ($vcvars64) {
        $pipExe = Join-Path $PSScriptRoot ".venv\Scripts\pip.exe"
        if (-not (Test-Path $pipExe)) { $pipExe = "pip.exe" }
        $cmd = "cd /d `"$PSScriptRoot`" && call `"$vcvars64`" && set FORCE_CMAKE=1 && set CMAKE_ARGS=-DGGML_VULKAN=ON -DGGML_NATIVE=ON -DGGML_AVX2=ON -DGGML_AVX512=ON && `"$pipExe`" install llama-cpp-python --no-binary :all: --no-cache-dir --no-build-isolation"
        cmd /c $cmd
    } else {
        pip install llama-cpp-python --no-binary :all: --no-cache-dir --no-build-isolation
    }
    if ($LASTEXITCODE -ne 0) { Write-Host "Build failed." -ForegroundColor Red; exit 1 }
    Write-Host "Done. Run: python server.py" -ForegroundColor Green
    exit 0
}

# ----- Default: wheels only -----
pip uninstall llama-cpp-python llama-cpp-python-win -y 2>&1 | Out-Null
pip install llama-cpp-python --only-binary :all: --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/cpu"
if ($LASTEXITCODE -ne 0) { pip install llama-cpp-python --only-binary :all: }
if ($LASTEXITCODE -ne 0) { pip install llama-cpp-python-win --only-binary :all: }
if ($LASTEXITCODE -ne 0) {
    Write-Host "No wheel for this Python. Use Python 3.12 or: .\install_llama_cpp_windows.ps1 -BuildFromSource" -ForegroundColor Red
    exit 1
}
Write-Host "Done. Run: python server.py" -ForegroundColor Green
