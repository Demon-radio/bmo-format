# Build + run the Rust core test suite with the zig/GNU workaround
# (no MSVC Build Tools on this machine). See docs/BUILD.md.
# Two phases: default (zero-dep build) and codec + signature features.
# Usage: powershell -File core/rust/run_tests.ps1
$ErrorActionPreference = "Stop"

$zigDir = "$env:LOCALAPPDATA\Programs\Python\Python313\Lib\site-packages\ziglang"
if (-not (Test-Path $zigDir)) {
    # fall back to whichever site-packages carries ziglang
    $zigDir = (Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Recurse -Filter zig.exe -ErrorAction SilentlyContinue |
               Select-Object -First 1).DirectoryName
    if (-not $zigDir) { throw "ziglang not found: pip install ziglang" }
}
$env:PATH = "$zigDir;$env:PATH"
# cargo-zigbuild cannot quote the '-o' path; keep the target dir space-free.
$env:CARGO_TARGET_DIR = "C:\bmo-target"
$deps = "$env:CARGO_TARGET_DIR\x86_64-pc-windows-gnu\debug\deps"
$filter = '^(bmo_core|conformance_hello|roundtrip|model|domains|codecs)-'

Push-Location $PSScriptRoot
try {
    foreach ($phase in @(@("default", @()), @("codecs-signatures", @("--features", "codecs,signatures")))) {
        $name = $phase[0]
        $feats = $phase[1]
        Write-Host "=== build: $name ==="
        # purge stale test exes so each phase runs only its own feature set
        Get-ChildItem $deps -Filter *.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match $filter } | Remove-Item -Force
        cargo zigbuild --target x86_64-pc-windows-gnu --lib --tests --bins @feats
        if ($LASTEXITCODE -ne 0) { throw "cargo zigbuild ($name) failed" }

        $fail = 0
        Get-ChildItem $deps -Filter *.exe | Where-Object { $_.Name -match $filter } |
            ForEach-Object {
                Write-Host "--- $($_.Name) ---"
                & $_.FullName
                if ($LASTEXITCODE -ne 0) { $fail++ }
            }
        if ($fail -gt 0) { throw "$fail Rust test binary/binaries failed ($name)" }
    }
    Write-Host "RUST SUITE GREEN (default + codecs/signatures)"
} finally {
    Pop-Location
}
