param(
  [string]$EnvFile = (Join-Path $PSScriptRoot ".env")
)

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
  throw "Environment file not found: $EnvFile"
}

$loadedCount = 0
$lineNumber = 0

foreach ($line in Get-Content -LiteralPath $EnvFile) {
  $lineNumber++
  $trimmedLine = $line.Trim()

  if (-not $trimmedLine -or $trimmedLine.StartsWith("#")) {
    continue
  }

  if ($trimmedLine -notmatch '^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    throw "Invalid environment assignment on line $lineNumber of $EnvFile"
  }

  $name = $Matches[1]
  $value = $Matches[2].Trim()

  if ($value.StartsWith('"') -or $value.StartsWith("'")) {
    $quote = $value.Substring(0, 1)
    if ($value.Length -lt 2 -or -not $value.EndsWith($quote)) {
      throw "Unclosed quoted value for '$name' on line $lineNumber of $EnvFile"
    }
    $value = $value.Substring(1, $value.Length - 2)
  }
  else {
    $value = ($value -replace '\s+#.*$', '').TrimEnd()
  }

  [Environment]::SetEnvironmentVariable($name, $value, "Process")
  $loadedCount++
}

Write-Output "Loaded $loadedCount environment variable(s) from $EnvFile into this PowerShell process."
