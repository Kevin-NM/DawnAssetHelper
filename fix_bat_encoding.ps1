$path = Join-Path $PSScriptRoot "open.bat"
$content = Get-Content $path -Raw
$content = $content -replace "`r?`n", "`r`n"
[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::ASCII)
