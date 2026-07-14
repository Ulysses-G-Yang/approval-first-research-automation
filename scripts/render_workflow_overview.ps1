param(
    [string]$PythonExecutable = "python",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName PresentationCore

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$renderer = Join-Path $root "scripts\render_product_preview.py"
$framesDir = Join-Path $root "docs\assets\.frames"
if (-not $OutputPath) {
    $OutputPath = Join-Path $root "docs\assets\workflow-overview.gif"
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)

New-Item -ItemType Directory -Path $framesDir -Force | Out-Null
try {
    $stages = @("collect", "verify", "compose")
    $framePaths = @()
    for ($index = 0; $index -lt $stages.Count; $index++) {
        $frame = Join-Path $framesDir ("{0:D2}-{1}.png" -f ($index + 1), $stages[$index])
        & $PythonExecutable $renderer --stage $stages[$index] --output $frame
        if ($LASTEXITCODE -ne 0) { throw "Preview renderer failed for stage $($stages[$index])." }
        $framePaths += $frame
    }

    $encoder = New-Object System.Windows.Media.Imaging.GifBitmapEncoder
    foreach ($framePath in $framePaths) {
        $stream = New-Object System.IO.FileStream($framePath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read)
        try {
            $decoder = New-Object System.Windows.Media.Imaging.PngBitmapDecoder(
                $stream,
                [System.Windows.Media.Imaging.BitmapCreateOptions]::PreservePixelFormat,
                [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad
            )
            $metadata = New-Object System.Windows.Media.Imaging.BitmapMetadata("gif")
            $metadata.SetQuery("/grctlext/Delay", [uint16]90)
            $metadata.SetQuery("/grctlext/Disposal", [byte]2)
            $bitmap = $decoder.Frames[0]
            $encoder.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($bitmap, $bitmap.Thumbnail, $metadata, $bitmap.ColorContexts))
        } finally {
            $stream.Dispose()
        }
    }

    $outputDirectory = Split-Path -Parent $OutputPath
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
    $output = New-Object System.IO.FileStream($OutputPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
    try {
        $encoder.Save($output)
    } finally {
        $output.Dispose()
    }

    $verification = New-Object System.IO.FileStream($OutputPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read)
    try {
        $decoder = New-Object System.Windows.Media.Imaging.GifBitmapDecoder(
            $verification,
            [System.Windows.Media.Imaging.BitmapCreateOptions]::PreservePixelFormat,
            [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad
        )
        if ($decoder.Frames.Count -ne $stages.Count) {
            throw "GIF verification failed: expected $($stages.Count) frames, found $($decoder.Frames.Count)."
        }
    } finally {
        $verification.Dispose()
    }

    Write-Output $OutputPath
} finally {
    $expectedFramesDir = Join-Path $root "docs\assets\.frames"
    if ($framesDir -eq $expectedFramesDir -and (Test-Path -LiteralPath $framesDir)) {
        Remove-Item -LiteralPath $framesDir -Recurse -Force
    }
}
