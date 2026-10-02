# Lisab Exploreri paremkliki menüüsse käsu "Tee eesti subtiitrid" (ainult praegusele kasutajale, admin-õigusi pole vaja).
# Eemaldamine: .\install_context_menu.ps1 -Uninstall
param([switch]$Uninstall)

$launcher = Join-Path $PSScriptRoot "estsub.cmd"
$label = "Tee eesti subtiitrid"
$icon = "$((Get-Command python -ErrorAction SilentlyContinue).Source),0"
$exts = ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts"

$targets = @(
    @{ Key = "HKCU:\Software\Classes\Directory\Background\shell\EstSub"; Arg = '"%V"' }  # kausta tühi ala
    @{ Key = "HKCU:\Software\Classes\Directory\shell\EstSub";            Arg = '"%1"' }  # kaust ise
)
foreach ($e in $exts) {
    $targets += @{ Key = "HKCU:\Software\Classes\SystemFileAssociations\$e\shell\EstSub"; Arg = '"%1"' }
}

foreach ($t in $targets) {
    if ($Uninstall) {
        if (Test-Path $t.Key) { Remove-Item $t.Key -Recurse -Force }
        continue
    }
    New-Item -Path "$($t.Key)\command" -Force | Out-Null
    Set-ItemProperty -Path $t.Key -Name "(default)" -Value $label
    Set-ItemProperty -Path $t.Key -Name "Icon" -Value $icon
    Set-ItemProperty -Path "$($t.Key)\command" -Name "(default)" -Value "`"$launcher`" $($t.Arg)"
}

if ($Uninstall) { "Menüükäsk eemaldatud." } else { "Menüükäsk '$label' lisatud ($($targets.Count) kohta)." }
