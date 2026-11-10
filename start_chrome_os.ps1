# Käivita Chrome oma profiiliga ja DevTools pordiga 9222 (Chrome ei luba porti vaikeprofiilil, seega tavalise brauseri sessiooni siia kaasa ei tule).
# Esimesel korral logi selles aknas OpenSubtitles.com-i sisse (käsitsi). Sessioon jääb profiili alles.
$profile = Join-Path $PSScriptRoot ".chrome_profile"
New-Item -ItemType Directory -Force $profile | Out-Null
$chrome = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe", "$env:LocalAppData\Google\Chrome\Application\chrome.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { Write-Error "chrome.exe ei leitud"; exit 1 }
Start-Process $chrome -ArgumentList "--remote-debugging-port=9222", "--user-data-dir=$((Resolve-Path $profile).Path)", "https://www.opensubtitles.com/en/users/sign_in"
Write-Host "Chrome käivitatud. Logi OpenSubtitles.com-i sisse ja jäta aken lahti."
