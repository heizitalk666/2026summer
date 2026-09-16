param([string]$In, [string]$OutDir, [int]$Width = 1600)
$ErrorActionPreference = 'Stop'
$In = (Resolve-Path $In).Path
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path
$pp = New-Object -ComObject PowerPoint.Application
try {
  # ReadOnly=1, Untitled=0, WithWindow=0
  $pres = $pp.Presentations.Open($In, 1, 0, 0)
  $sw = $pres.PageSetup.SlideWidth; $sh = $pres.PageSetup.SlideHeight
  $h = [int]($Width * $sh / $sw)
  "slide size (pt): $sw x $sh ; slides: $($pres.Slides.Count)"
  foreach ($s in $pres.Slides) {
    $p = Join-Path $OutDir ("slide-{0:D2}.png" -f $s.SlideIndex)
    $s.Export($p, "PNG", $Width, $h)
  }
  $pres.Close()
} finally {
  $pp.Quit()
  [System.Runtime.InteropServices.Marshal]::ReleaseComObject($pp) | Out-Null
}
