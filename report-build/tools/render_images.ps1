# Renders every HTML page in the given folder to a PNG with headless Microsoft Edge.
#   powershell -File report-build\tools\render_images.ps1 <folder> <width> [<height>]
param([string]$Folder, [int]$Width, [int]$Height = 900)
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$profileDir = Join-Path $env:TEMP "edge-render-storage-lab"
Get-ChildItem (Join-Path $Folder "*.html") | ForEach-Object {
    $h = $Height
    $sizeFile = $_.FullName -replace '\.html$', '.size'
    if (Test-Path $sizeFile) { $h = [int](Get-Content $sizeFile) }
    $png = $_.FullName -replace '\.html$', '.png'
    & $edge --headless=new --disable-gpu --hide-scrollbars --user-data-dir="$profileDir" `
        --screenshot="$png" --window-size="$Width,$h" ([System.Uri]$_.FullName).AbsoluteUri 2>$null | Out-Null
    Write-Output "$($_.Name) -> $png"
}
