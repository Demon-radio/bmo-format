# BMO double-click opener. Ensures the Studio server runs, then opens the
# clicked file in the browser. Called by open-bmo.bat (file association).
param([string]$File)
$ErrorActionPreference = "Stop"
$Port = 8077
$Studio = Join-Path $PSScriptRoot "server.py"

function Server-Up {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/ping" -TimeoutSec 2 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch { return $false }
}

if (-not (Server-Up)) {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
    if (-not $py) {
        [System.Windows.Forms.MessageBox]::Show("Python not found. Install Python 3.11+ to open .bmo files.") | Out-Null
        exit 1
    }
    Start-Process -FilePath $py.Source -ArgumentList "`"$Studio`" --port $Port --no-browser" `
        -WindowStyle Hidden -WorkingDirectory (Split-Path $Studio -Parent)
    for ($i = 0; $i -lt 75 -and -not (Server-Up); $i++) { Start-Sleep -Milliseconds 200 }
}
if (-not (Server-Up)) {
    [System.Windows.Forms.MessageBox]::Show("BMO Studio did not start.") | Out-Null
    exit 1
}
Add-Type -AssemblyName System.Web
$url = "http://127.0.0.1:$Port/open?path=" + [System.Web.HttpUtility]::UrlEncode($File)
Start-Process $url
