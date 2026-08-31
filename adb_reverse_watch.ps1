# Keeps `adb reverse tcp:8000 tcp:8000` alive for the VeNdO Android app.
# adb reverse only lasts until the device disconnects from adb (USB
# unplug/replug, screen-lock reauth, adb server restart) - this loop
# just re-asserts it every few seconds so it's always there, without
# anyone having to notice it dropped or run the command by hand again.
$adb = "C:\Android\Sdk\platform-tools\adb.exe"

Write-Output "Watching for the phone and keeping adb reverse tcp:8000 alive. Ctrl+C to stop."
while ($true) {
    $devices = & $adb devices 2>$null | Select-String "\tdevice$"
    if ($devices) {
        & $adb reverse tcp:8000 tcp:8000 2>$null | Out-Null
    }
    Start-Sleep -Seconds 3
}
