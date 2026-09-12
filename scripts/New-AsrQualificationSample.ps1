# Generate a self-made ASR qualification sample with Windows SAPI text-to-speech.
#
# Why: the shared qualification corpus must stay self-made and must not contain
# customer data (scripts/asr_qualification_manifest.py SOURCE_DECLARATION), while
# the WhisperX profile moves to a no-hotword decode. The absolute gates
# (standard-code recall, BIM-term recall, content coverage) must still hold on a
# corpus that speaks standard codes and BIM terms naturally in longer audio, not
# only in a 4-second clipped phrase.
#
# This script turns a sentence list into one 16 kHz mono 16-bit PCM WAV plus a
# JSON timing record (per-sentence start/end offsets), so the corpus annotation
# can be built from the generated audio instead of guessed timings.
#
# Usage:
#   pwsh -NoProfile -File scripts/New-AsrQualificationSample.ps1 `
#     -SentenceFile sentences.txt -WavePath sample.wav -TimingPath sample.timing.json `
#     -VoiceName 'Microsoft Huihui Desktop' -GapMs 350

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SentenceFile,
    [Parameter(Mandatory = $true)][string]$WavePath,
    [Parameter(Mandatory = $true)][string]$TimingPath,
    [string]$VoiceName = 'Microsoft Huihui Desktop',
    [int]$GapMs = 350,
    [int]$Rate = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Speech

if (-not (Test-Path -LiteralPath $SentenceFile -PathType Leaf)) {
    throw "sentence file not found: $SentenceFile"
}
$sentences = @(
    Get-Content -LiteralPath $SentenceFile -Encoding UTF8 |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -ne '' }
)
if ($sentences.Count -eq 0) {
    throw 'sentence file contains no usable sentences'
}

$synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voice = $synthesizer.GetInstalledVoices() |
        Where-Object { $_.VoiceInfo.Name -eq $VoiceName } |
        Select-Object -First 1
    if ($null -eq $voice) {
        throw "voice is not installed: $VoiceName"
    }
    $synthesizer.SelectVoice($VoiceName)
    $synthesizer.Rate = $Rate
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)

    $workRoot = Join-Path ([IO.Path]::GetTempPath()) ("asr-qualification-sample-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $workRoot | Out-Null
    try {
        $pcmChunks = New-Object System.Collections.Generic.List[byte[]]
        $timings = New-Object System.Collections.Generic.List[object]
        $cursorMs = 0
        $gapSamples = [int]($GapMs * 16)
        $silence = New-Object byte[] ($gapSamples * 2)

        for ($index = 0; $index -lt $sentences.Count; $index++) {
            $sentence = $sentences[$index]
            $partPath = Join-Path $workRoot ("part-{0:d3}.wav" -f $index)
            $synthesizer.SetOutputToWaveFile($partPath, $format)
            $synthesizer.Speak($sentence)
            $synthesizer.SetOutputToNull()

            $bytes = [IO.File]::ReadAllBytes($partPath)
            # Skip the 44-byte canonical WAV header written by SAPI.
            $data = New-Object byte[] ($bytes.Length - 44)
            [Array]::Copy($bytes, 44, $data, 0, $data.Length)
            $partMs = [int][math]::Round($data.Length / 32.0)

            $timings.Add([ordered]@{
                    index = $index
                    start_ms = $cursorMs
                    end_ms = ($cursorMs + $partMs)
                    text = $sentence
                })
            $pcmChunks.Add($data)
            $cursorMs += $partMs
            if ($index -lt $sentences.Count - 1) {
                $pcmChunks.Add($silence)
                $cursorMs += $GapMs
            }
        }

        $totalBytes = 0
        foreach ($chunk in $pcmChunks) { $totalBytes += $chunk.Length }
        $wavePathFull = [IO.Path]::GetFullPath($WavePath)
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $wavePathFull) | Out-Null
        $stream = [IO.File]::Create($wavePathFull)
        try {
            $writer = New-Object IO.BinaryWriter($stream)
            try {
                $writer.Write([Text.Encoding]::ASCII.GetBytes('RIFF'))
                $writer.Write([int](36 + $totalBytes))
                $writer.Write([Text.Encoding]::ASCII.GetBytes('WAVE'))
                $writer.Write([Text.Encoding]::ASCII.GetBytes('fmt '))
                $writer.Write([int]16)
                $writer.Write([int16]1)
                $writer.Write([int16]1)
                $writer.Write([int]16000)
                $writer.Write([int]32000)
                $writer.Write([int16]2)
                $writer.Write([int16]16)
                $writer.Write([Text.Encoding]::ASCII.GetBytes('data'))
                $writer.Write([int]$totalBytes)
                foreach ($chunk in $pcmChunks) { $writer.Write($chunk) }
            }
            finally { $writer.Dispose() }
        }
        finally { $stream.Dispose() }

        $payload = [ordered]@{
            schema_version = 'asr-qualification-tts-sample/1'
            voice = $VoiceName
            rate = $Rate
            gap_ms = $GapMs
            duration_ms = $cursorMs
            sentence_count = $sentences.Count
            sentences = $timings
        }
        [IO.File]::WriteAllText(
            [IO.Path]::GetFullPath($TimingPath),
            ($payload | ConvertTo-Json -Depth 4),
            (New-Object Text.UTF8Encoding($false))
        )
        Write-Host ("ASR_QUALIFICATION_SAMPLE status=ok sentences={0} duration_ms={1} path={2}" -f $sentences.Count, $cursorMs, $wavePathFull)
    }
    finally {
        Remove-Item -Recurse -Force $workRoot -ErrorAction SilentlyContinue
    }
}
finally {
    $synthesizer.Dispose()
}
