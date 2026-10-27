# Clickable elements of a window via Windows UI Automation (read-only).
# Output lines: <type>|<x>|<y>|<w>|<h>|<name>   (screen coordinates; only visible, named elements)
param([long]$Hwnd = 0, [int]$Max = 250)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -Namespace W -Name U -MemberDefinition '[DllImport("user32.dll")] public static extern System.IntPtr GetForegroundWindow();'
if ($Hwnd -eq 0) { $Hwnd = [long][W.U]::GetForegroundWindow() }
$AE = [System.Windows.Automation.AutomationElement]
$CT = [System.Windows.Automation.ControlType]
$types = @($CT::Button, $CT::Hyperlink, $CT::ListItem, $CT::MenuItem, $CT::TabItem, $CT::CheckBox, $CT::RadioButton,
           $CT::Edit, $CT::ComboBox, $CT::TreeItem, $CT::SplitButton)
$conds = $types | ForEach-Object { New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, $_) }
$or = New-Object System.Windows.Automation.OrCondition($conds)
$root = $AE::FromHandle([System.IntPtr]$Hwnd)
$win = $root.Current.BoundingRectangle
$n = 0
foreach ($e in $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $or)) {
    $c = $e.Current
    if ($c.IsOffscreen -or -not $c.Name) { continue }
    $r = $c.BoundingRectangle
    if ($r.Width -lt 4 -or $r.Height -lt 4) { continue }
    if ($r.Bottom -lt $win.Top -or $r.Top -gt $win.Bottom -or $r.Right -lt $win.Left -or $r.Left -gt $win.Right) { continue }
    $name = ($c.Name -replace '\s+', ' ').Trim()
    if ($name.Length -gt 90) { $name = $name.Substring(0, 90) }
    "$($c.ControlType.ProgrammaticName -replace 'ControlType\.', '')|$([int]$r.X)|$([int]$r.Y)|$([int]$r.Width)|$([int]$r.Height)|$name"
    $n++
    if ($n -ge $Max) { break }
}
