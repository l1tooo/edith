# Browser tabs via Windows UI Automation.
#   -Mode list                      -> lines "<process>|<hwnd>|<index>|<title>" (only real browser tabs, not tabs inside pages)
#   -Mode select -Hwnd H -Index I   -> selects that tab and brings the window to front; prints "ok" or "fail"
param([string]$Mode = 'list', [long]$Hwnd = 0, [int]$Index = -1, [string[]]$Browsers = @('firefox', 'chrome', 'msedge'))
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8  # кириллица в названиях вкладок
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -Namespace W -Name U -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr h, int c);
[DllImport("user32.dll")] public static extern bool IsIconic(System.IntPtr h);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(System.IntPtr h);
'@
$AE = [System.Windows.Automation.AutomationElement]
$tabCond = New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, [System.Windows.Automation.ControlType]::TabItem)
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker

function Get-BrowserTabs([System.IntPtr]$h) {
    $root = $AE::FromHandle($h)
    foreach ($t in $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $tabCond)) {
        # skip tabs that live inside a web page (their ancestors include a Document)
        $node = $walker.GetParent($t); $inPage = $false
        for ($i = 0; $i -lt 12 -and $node -and -not $node.Equals($root); $i++) {
            if ($node.Current.ControlType -eq [System.Windows.Automation.ControlType]::Document) { $inPage = $true; break }
            $node = $walker.GetParent($node)
        }
        if (-not $inPage) { $t }
    }
}

if ($Mode -eq 'list') {
    foreach ($b in $Browsers) {
        foreach ($p in (Get-Process $b -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 })) {
            $i = 0
            foreach ($t in (Get-BrowserTabs $p.MainWindowHandle)) { "$b|$($p.MainWindowHandle)|$i|$($t.Current.Name)"; $i++ }
        }
    }
} else {
    $h = [System.IntPtr]$Hwnd
    $tab = @(Get-BrowserTabs $h)[$Index]
    if (-not $tab) { 'fail'; exit }
    if ([W.U]::IsIconic($h)) { [void][W.U]::ShowWindow($h, 9) }
    [void][W.U]::SetForegroundWindow($h)
    try { $tab.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select() }
    catch { try { $tab.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() } catch { 'fail'; exit } }
    'ok'
}
