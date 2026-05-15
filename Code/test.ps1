# System Performance Monitor
# Logs CPU, memory, disk, and network stats at a configurable interval

param(
    [int]$IntervalSeconds = 5,
    [int]$DurationMinutes = 0,     # 0 = run indefinitely
    [string]$LogFile = "",          # optional CSV log path
    [switch]$NoColor
)

function Get-ColorCode {
    param([double]$Value, [double]$WarnThreshold = 70, [double]$CritThreshold = 90)
    if ($NoColor) { return "White" }
    if ($Value -ge $CritThreshold) { return "Red" }
    if ($Value -ge $WarnThreshold) { return "Yellow" }
    return "Green"
}

function Format-Bytes {
    param([long]$Bytes)
    switch ($Bytes) {
        { $_ -ge 1GB } { return "{0:N2} GB" -f ($_ / 1GB) }
        { $_ -ge 1MB } { return "{0:N2} MB" -f ($_ / 1MB) }
        { $_ -ge 1KB } { return "{0:N2} KB" -f ($_ / 1KB) }
        default        { return "$_ B" }
    }
}

function Get-CpuUsage {
    $cpu = Get-Counter '\Processor(_Total)\% Processor Time' -SampleInterval 1 -MaxSamples 1
    return [math]::Round($cpu.CounterSamples[0].CookedValue, 1)
}

function Get-MemoryInfo {
    $os = Get-CimInstance Win32_OperatingSystem
    $total = $os.TotalVisibleMemorySize * 1KB
    $free  = $os.FreePhysicalMemory     * 1KB
    $used  = $total - $free
    $pct   = [math]::Round(($used / $total) * 100, 1)
    return [PSCustomObject]@{
        TotalBytes = $total
        UsedBytes  = $used
        FreeBytes  = $free
        UsedPct    = $pct
    }
}

function Get-DiskInfo {
    Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Used -ne $null } | ForEach-Object {
        $total = $_.Used + $_.Free
        if ($total -eq 0) { return }
        $pct = [math]::Round(($_.Used / $total) * 100, 1)
        [PSCustomObject]@{
            Drive    = $_.Name
            UsedPct  = $pct
            Used     = $_.Used
            Free     = $_.Free
            Total    = $total
        }
    }
}

function Get-NetworkInfo {
    $adapters = Get-NetAdapterStatistics | Where-Object { $_.Name -notmatch 'Loopback' }
    $adapters | ForEach-Object {
        [PSCustomObject]@{
            Adapter        = $_.Name
            BytesSent      = $_.SentBytes
            BytesReceived  = $_.ReceivedBytes
        }
    }
}

function Get-TopProcesses {
    param([int]$Top = 5)
    Get-Process | Sort-Object CPU -Descending | Select-Object -First $Top |
        Select-Object Name,
            @{N='CPU(s)'; E={ [math]::Round($_.CPU, 1) }},
            @{N='RAM(MB)'; E={ [math]::Round($_.WorkingSet64 / 1MB, 1) }}
}

function Write-Header {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "`n===== System Performance Monitor  $ts =====" -ForegroundColor Cyan
    Write-Host "  Interval: ${IntervalSeconds}s   Duration: $(if ($DurationMinutes -eq 0) { 'indefinite' } else { "${DurationMinutes}m" })   Log: $(if ($LogFile) { $LogFile } else { 'none' })"
    Write-Host ("-" * 60)
}

function Write-CsvHeader {
    "Timestamp,CPU_Pct,Mem_Pct,Mem_Used_GB,Mem_Free_GB" | Out-File -FilePath $LogFile -Encoding UTF8
}

function Write-CsvRow {
    param($ts, $cpu, $mem)
    "$ts,$cpu,$($mem.UsedPct),$([math]::Round($mem.UsedBytes/1GB,2)),$([math]::Round($mem.FreeBytes/1GB,2))" |
        Add-Content -Path $LogFile
}

# ------ Setup ------
if ($LogFile) { Write-CsvHeader }

$stopAt = if ($DurationMinutes -gt 0) { (Get-Date).AddMinutes($DurationMinutes) } else { $null }

Write-Host "Starting monitor. Press Ctrl+C to stop." -ForegroundColor Cyan

try {
    while ($true) {
        if ($stopAt -and (Get-Date) -ge $stopAt) { break }

        $ts  = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        $cpu = Get-CpuUsage
        $mem = Get-MemoryInfo
        $disks = Get-DiskInfo
        $nets  = Get-NetworkInfo

        Clear-Host
        Write-Header

        # CPU
        Write-Host "`n[CPU]" -ForegroundColor White
        Write-Host ("  Usage: {0,5}%" -f $cpu) -ForegroundColor (Get-ColorCode $cpu)

        # Memory
        Write-Host "`n[Memory]" -ForegroundColor White
        Write-Host ("  Used:  {0,8}  /  {1}  ({2}%)" -f `
            (Format-Bytes $mem.UsedBytes), (Format-Bytes $mem.TotalBytes), $mem.UsedPct) `
            -ForegroundColor (Get-ColorCode $mem.UsedPct)
        Write-Host ("  Free:  {0,8}" -f (Format-Bytes $mem.FreeBytes))

        # Disks
        Write-Host "`n[Disks]" -ForegroundColor White
        foreach ($d in $disks) {
            Write-Host ("  {0}:  {1,5}% used  ({2} / {3})" -f `
                $d.Drive, $d.UsedPct, (Format-Bytes $d.Used), (Format-Bytes $d.Total)) `
                -ForegroundColor (Get-ColorCode $d.UsedPct 80 95)
        }

        # Network (totals since boot — useful as deltas between samples)
        Write-Host "`n[Network Adapters]" -ForegroundColor White
        foreach ($n in $nets) {
            Write-Host ("  {0,-28} Sent: {1,10}  Recv: {2,10}" -f `
                $n.Adapter, (Format-Bytes $n.BytesSent), (Format-Bytes $n.BytesReceived))
        }

        # Top processes by CPU
        Write-Host "`n[Top Processes by CPU]" -ForegroundColor White
        Get-TopProcesses | Format-Table -AutoSize | Out-String | Write-Host

        if ($LogFile) { Write-CsvRow $ts $cpu $mem }

        Start-Sleep -Seconds $IntervalSeconds
    }
}
catch [System.Management.Automation.PipelineStoppedException] {
    # Ctrl+C — clean exit
}
finally {
    Write-Host "`nMonitor stopped." -ForegroundColor Cyan
    if ($LogFile) { Write-Host "Log saved to: $LogFile" -ForegroundColor Cyan }
}
